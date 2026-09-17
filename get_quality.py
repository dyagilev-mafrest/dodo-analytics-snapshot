# Dodo IS -> PostgreSQL (Supabase): customer_ratings_daily + customer_feedback
#
# Endpoints:
# - customer-feedback/customer-ratings — avgDineInOrderRate/avgDeliveryOrderRate +
#   counts. from/to — ДАТЫ (время отбрасывается, `to` округляется до конца суток),
#   разбивки по дням в ответе нет, поэтому дневная серия строится запросом на
#   каждый день — по всем юнитам сразу. Подробности и история бага — в
#   docstring fetch_ratings().
# - dodopizza/customer-feedback/recent-feedbacks — raw feedback (orderRate,
#   feedbackComment), no channel field. VERIFIED LIVE: this endpoint's from/to
#   params are ignored — it always returns the latest ~10 feedbacks per unit
#   regardless of the requested window. So there's no way to backfill feedback
#   history, and there's no point calling it per-day: this script polls it once
#   per run (latest snapshot), grouped by the feedbacks' actual orderCreatedAt
#   date to fetch the matching day's accounting/sales (via
#   get_product_sales.fetch_sales) and recover salesChannel by orderId, then
#   classifies into a problem category heuristically (see classify_problem()) —
#   Dodo IS doesn't expose an exact problem-category/responsible-party field via
#   the API (see project memory), this is a keyword approximation. Upserts are
#   keyed by order_id, so re-polling overlapping "latest 10" is harmless.
#
# Used by the pulse-week "Клиентский опыт" block: rating cards + trend charts
# (customer_ratings_daily), "ТОП проблем" tables per channel (customer_feedback).
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token
from get_product_sales import fetch_sales
from db import get_connection, get_cursor

load_dotenv()
RATINGS_BASE = "https://api.dodois.io/customer-feedback"
FEEDBACK_BASE = "https://api.dodois.io/dodopizza/customer-feedback"
UNITS_FILE = "yakutsk_units.json"


def make_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def api_get(url: str, params: dict, headers: dict, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=60)
            if r.status_code == 401:
                stale = headers.get("Authorization", "").removeprefix("Bearer ")
                headers["Authorization"] = f"Bearer {get_access_token(force_refresh=True, _stale_token=stale)}"
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1}/{retries}: {e}")
            time.sleep(5 * (attempt + 1))
    return {}


def fetch_ratings(unit_ids: str, day: str, headers: dict) -> list[dict]:
    """Рейтинги за ОДИН день по всем юнитам разом.

    ⚠️ from/to этого эндпоинта — ДАТЫ, а не моменты времени: время в них
    отбрасывается, `to` округляется до конца своих суток. Проверено живьём
    (2026-09-02, Якутск-1, from=2026-08-25): to=26T00:00:01, 26T07:00:00 и
    26T23:59:59 дают один и тот же ответ (56/78 оценок), а to=27T07:00 — уже
    следующий (84/126). Ровно это написано и в официальной спеке:
    docs/dodo-is-openapi/ratings-api.yaml, «Правило приведения дат».

    Раньше здесь стояло `to = day+1 T07:00:00` — попытка добрать отзывы,
    приходящие после полуночи. По факту это тянуло В КАЖДУЮ дневную строку
    целиком следующие сутки: соседние дни перекрывались, число оценок за неделю
    выходило в 1.8 раза больше настоящего (338 против 184 у Якутск-1 за
    24–30.08.2026), а средняя размазывалась по сдвинутому окну. Отсюда и брались
    расхождения с дашбордом Dodo IS.

    Правильное дневное окно — from = to = day. Проверено: сумма таких дней
    сходится с одним недельным запросом ровно (184/305 оценок, 4.76/4.73).
    """
    data = api_get(f"{RATINGS_BASE}/customer-ratings", {
        "units": unit_ids,
        "from": day,
        "to": day,
    }, headers)
    return data.get("customerRatings", [])


def fetch_feedbacks(unit_id: str, headers: dict) -> list[dict]:
    # from/to are ignored by this endpoint (verified live) — it always returns
    # the latest ~10 feedbacks for the unit. Passing a wide dummy window anyway
    # in case that ever changes upstream.
    data = api_get(f"{FEEDBACK_BASE}/recent-feedbacks", {
        "units": unit_id,
        "from": "2020-01-01T00:00:00",
        "to": f"{date.today().isoformat()}T23:59:59",
    }, headers)
    return data.get("orderFeedbacks", [])


# Ключевые слова — эвристика, не точный справочник Dodo IS (см. заголовок файла).
# Порядок важен: первое совпадение побеждает.
DELIVERY_PROBLEM_KEYWORDS = [
    ("Курьер опоздал", ["опозда", "долго вез", "долго ехал", "долго достав"]),
    ("Начинка растеклась или вытекла", ["начинка вытек", "начинка растек", "потекло"]),
    ("Продукт холодный", ["холодн", "остыл", "еле тёпл", "еле тепл"]),
    ("Пролили напиток при доставке", ["пролил", "разлил"]),
    ("Продукт мятый", ["мят", "помял", "деформир"]),
    ("Не привезли или перепутали приборы, трубочки в заказе на доставку", ["прибор", "трубочк", "салфетк"]),
    ("Клиент не получил компенсацию за опоздание", ["компенсац", "сертификат"]),
    ("Заказ привезли или выдали по частям", ["по частям", "не все привезл", "не всё привезл", "часть заказа"]),
    ("Клиент не получил продукт из заказа", ["не доложили", "не положили", "забыли положить", "не хватает", "нет в заказе", "не докомплект"]),
]

RESTAURANT_PROBLEM_KEYWORDS = [
    ("Долгое ожидание заказа в ресторане/на самовывоз", ["долго ждал", "долгое ожидан", "час ждал", "долго готов"]),
    ("Продукт холодный", ["холодн", "остыл"]),
    ("Не выдали или перепутали салфетки, приборы, трубочки в заказе в ресторане", ["прибор", "трубочк", "салфетк"]),
    ("В зале, туалетной или детской комнате грязно", ["грязно", "туалет", "антисанитар"]),
    ("Некорректное поведение сотрудника", ["хамств", "нагруб", "неуместн", "оскорб", "нахам"]),
    ("Неполадки в работе оборудования", ["не работал", "сломан", "неисправ"]),
    ("Не отдали заказ или отдали чужой заказ", ["чужой заказ", "не тот заказ", "перепутали заказ", "отдали не мой"]),
    ("Продукт мятый", ["мят", "помял", "деформир"]),
    ("Начинка растеклась или вытекла", ["начинка вытек", "начинка растек", "потекло"]),
    ("Клиент не получил продукт из заказа", ["не доложили", "не положили", "забыли положить", "не хватает", "нет в заказе"]),
]


def classify_problem(comment: str | None, channel: str | None) -> str | None:
    if not comment:
        return None
    text = comment.lower()
    keywords = DELIVERY_PROBLEM_KEYWORDS if channel == "Delivery" else RESTAURANT_PROBLEM_KEYWORDS
    for category, words in keywords:
        if any(w in text for w in words):
            return category
    return "Другое"


def build_channel_map(unit_id: str, day: str, headers: dict) -> dict[str, str]:
    # accounting/sales returns orderId lowercase, recent-feedbacks returns it
    # uppercase — normalize both sides to match.
    orders = fetch_sales(unit_id, day, headers)
    return {o["orderId"].lower(): o.get("salesChannel") for o in orders if o.get("orderId")}


def upsert_ratings(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO customer_ratings_daily
            (date, unit_id, unit_name, avg_dine_in_rate, avg_delivery_rate, dine_in_rate_count, delivery_rate_count)
        VALUES %s
        ON CONFLICT (date, unit_id) DO UPDATE SET
            unit_name            = EXCLUDED.unit_name,
            avg_dine_in_rate      = EXCLUDED.avg_dine_in_rate,
            avg_delivery_rate     = EXCLUDED.avg_delivery_rate,
            dine_in_rate_count    = EXCLUDED.dine_in_rate_count,
            delivery_rate_count   = EXCLUDED.delivery_rate_count
    """, [
        (
            row["date"], row["unit_id"], row["unit_name"], row["avg_dine_in_rate"],
            row["avg_delivery_rate"], row["dine_in_rate_count"], row["delivery_rate_count"],
        )
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def upsert_feedback(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO customer_feedback
            (order_id, unit_id, date, order_rate, feedback_comment, sales_channel,
             problem_category, order_created_at, feedback_created_at)
        VALUES %s
        ON CONFLICT (order_id) DO UPDATE SET
            order_rate           = EXCLUDED.order_rate,
            feedback_comment     = EXCLUDED.feedback_comment,
            sales_channel        = EXCLUDED.sales_channel,
            problem_category     = EXCLUDED.problem_category,
            feedback_created_at  = EXCLUDED.feedback_created_at
    """, [
        (
            row["order_id"], row["unit_id"], row["date"], row["order_rate"], row["feedback_comment"],
            row["sales_channel"], row["problem_category"], row["order_created_at"], row["feedback_created_at"],
        )
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def date_range(start: str, end: str):
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        yield str(d)
        d += timedelta(days=1)


def load_ratings(units: list[dict], from_date: str, to_date: str, workers: int = 8):
    """Один запрос на день на ВСЕ юниты сразу — эндпоинт принимает до 100 units и
    всё равно не даёт разбивки по дням, так что дневная серия строится по дням, а
    не по юнитам (было 7 запросов на день, стало 1).

    Дни тянутся параллельно и пишутся пачками: на бэкфилле в 961 день
    последовательный вариант с отдельным соединением к БД на каждый день занимал
    больше двух часов, здесь — минуты. На штатном 7-дневном окне разницы почти нет.
    """
    days = list(date_range(from_date, to_date))
    unit_ids = ",".join(u["id"] for u in units)
    names = {u["id"].lower(): u.get("name") for u in units}
    headers = make_headers()

    def fetch_day(day: str) -> list[dict]:
        return [{
            "date": day,
            "unit_id": r["unitId"].lower(),
            "unit_name": names.get(r["unitId"].lower()),
            "avg_dine_in_rate": r.get("avgDineInOrderRate"),
            "avg_delivery_rate": r.get("avgDeliveryOrderRate"),
            "dine_in_rate_count": r.get("dineInRateCount") or 0,
            "delivery_rate_count": r.get("deliveryRateCount") or 0,
        } for r in fetch_ratings(unit_ids, day, headers)]

    CHUNK = 60
    done = 0
    for i in range(0, len(days), CHUNK):
        chunk = days[i:i + CHUNK]
        rows = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for day_rows in pool.map(fetch_day, chunk):
                rows.extend(day_rows)
        upsert_ratings(rows)
        done += len(chunk)
        print(f"[ratings {done}/{len(days)}] по {chunk[-1]}: строк {len(rows)}", flush=True)


def load_feedback(units: list[dict]):
    print("\n[feedback: polling latest per unit]", flush=True)
    headers = make_headers()

    raw_by_unit: dict[str, list[dict]] = {}
    needed_days: dict[str, set[str]] = defaultdict(set)  # unit_id -> set of order dates to fetch channel for
    for unit in units:
        fbs = fetch_feedbacks(unit["id"], headers)
        raw_by_unit[unit["id"]] = fbs
        for fb in fbs:
            created = fb.get("orderCreatedAt")
            if created:
                needed_days[unit["id"]].add(created[:10])
        print(f"  {unit.get('name')}: {len(fbs)} feedbacks, {len(needed_days[unit['id']])} distinct order days", flush=True)

    channel_maps: dict[tuple[str, str], dict[str, str]] = {}
    for unit_id, days in needed_days.items():
        for day in days:
            channel_maps[(unit_id, day)] = build_channel_map(unit_id, day, headers)

    rows = []
    for unit in units:
        for fb in raw_by_unit[unit["id"]]:
            created = fb.get("orderCreatedAt")
            order_day = created[:10] if created else None
            channel = None
            if order_day:
                channel = channel_maps.get((unit["id"], order_day), {}).get(fb.get("orderId", "").lower())
            rate = fb.get("orderRate")
            comment = fb.get("feedbackComment")
            rows.append({
                "order_id": fb["orderId"], "unit_id": unit["id"],
                "date": order_day or fb.get("feedbackCreatedAt", "")[:10],
                "order_rate": rate, "feedback_comment": comment, "sales_channel": channel,
                "problem_category": classify_problem(comment, channel) if rate is not None and rate <= 3 else None,
                "order_created_at": created, "feedback_created_at": fb.get("feedbackCreatedAt"),
            })

    upsert_feedback(rows)
    print(f"  upserted {len(rows)} feedback rows", flush=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="Range for customer_ratings_daily (customer-feedback endpoint has no daily history)")
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    load_ratings(units, args.from_date, args.to_date)
    load_feedback(units)  # always polls "latest", independent of --from-date/--to-date
    print("Done.")


if __name__ == "__main__":
    main()
