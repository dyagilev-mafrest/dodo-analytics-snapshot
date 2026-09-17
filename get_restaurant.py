# Dodo IS -> PostgreSQL (Supabase): restaurant_stats (per unit per day)
#
# Endpoints used:
#   production/orders-handover-time - per-order data with cookingTime + trackingPendingTime + salesChannel
#   Filter: salesChannel in ("Dine-in", "Takeaway") — ресторан + самовывоз, доставка не входит
#   Denominator excludes orders whose WHOLE time is zero, not those with cookingTime <= 0:
#   официальное определение — «заказы в ресторане с НЕНУЛЕВЫМ временем приготовления», а
#   время это ожидание + приготовление + сборка. Заказ с cookingTime = 0, но ненулевым
#   trackingPendingTime в знаменатель Dodo IS входит (проверено 11.09.2026: фильтр по
#   cookingTime давал 14 250 заказов за неделю 31.08–06.09 против эталонных 14 655,
#   фильтр по сумме даёт 14 642 — расхождение 0,09%).
#   Официальная методология Dodo IS: время приготовления в ресторане = ожидание (trackingPendingTime)
#   + приготовление + сборка. cookingTime уже включает приготовление+сборку, поэтому порог 15 мин
#   считается по (cookingTime + trackingPendingTime); assemblyTime отдельно НЕ добавляется (уже внутри cookingTime).
#   Aggregated: dine_in_orders, cooking_time_sum (seconds, только cookingTime),
#   tracking_pending_sum (seconds) — (cooking_time_sum + tracking_pending_sum) / dine_in_orders
#   is the full order-to-handover cycle, orders_over_15min
import os, json, time
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token
from db import get_connection, get_cursor, placeholder

load_dotenv()
BASE = "https://api.dodois.io/dodopizza/ru"
UNITS_FILE = "yakutsk_units.json"


def make_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}"}


def api_get(path: str, params: dict, headers: dict, retries: int = 3) -> dict:
    url = f"{BASE}/{path}"
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=90)
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


def fetch_all_pages(path: str, key: str, unit_id: str, day: str, headers: dict) -> list:
    items = []
    skip = 0
    take = 1000
    while True:
        data = api_get(path, {
            "units": unit_id,
            "from": f"{day}T00:00:00",
            "to": f"{day}T23:59:59",
            "skip": skip,
            "take": take,
        }, headers)
        batch = data.get(key, [])
        items.extend(batch)
        if data.get("isEndOfListReached", True):
            break
        skip += take
    return items


def aggregate_dine_in(orders: list) -> tuple:
    count = 0
    cooking_sum = 0
    tracking_pending_sum = 0
    over_15 = 0
    for o in orders:
        if o.get("salesChannel") not in ("Dine-in", "Takeaway"):
            continue
        cooking_sec = o.get("cookingTime") or 0
        tracking_pending_sec = o.get("trackingPendingTime") or 0
        # Ноль означает «заказ не проходил через трекер» — по официальному
        # определению такой заказ не попадает в знаменатель. Но смотреть надо на
        # ВСЁ время, а не на одну готовку: заказ, который постоял в ожидании и
        # ушёл без готовки, у Dodo IS считается. Фильтр по cookingTime терял
        # 2,8% знаменателя и завышал долю > 15 мин на 0,7 п.п.
        if cooking_sec + tracking_pending_sec <= 0:
            continue
        count += 1
        cooking_sum += cooking_sec
        tracking_pending_sum += tracking_pending_sec
        if (cooking_sec + tracking_pending_sec) > 900:
            over_15 += 1
    return count, cooking_sum, tracking_pending_sum, over_15


def fetch_day(unit_id: str, unit_name: str, day: str, headers: dict):
    try:
        orders = fetch_all_pages(
            "production/orders-handover-time", "ordersHandoverTime",
            unit_id, day, headers,
        )
        count, cooking_sum, tracking_pending_sum, over_15 = aggregate_dine_in(orders)
        if count == 0:
            return None
        return {
            "date": day,
            "unit_id": unit_id,
            "unit_name": unit_name,
            "dine_in_orders": count,
            "cooking_time_sum": cooking_sum,
            "tracking_pending_sum": tracking_pending_sum,
            "orders_over_15min": over_15,
        }
    except Exception as e:
        print(f"  ERROR {unit_name} {day}: {e}")
        return None


def ensure_table():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS restaurant_stats (
            date        date NOT NULL,
            unit_id     text NOT NULL,
            unit_name   text,
            dine_in_orders    integer DEFAULT 0,
            cooking_time_sum  real    DEFAULT 0,
            tracking_pending_sum real DEFAULT 0,
            orders_over_15min integer DEFAULT 0,
            PRIMARY KEY (date, unit_id)
        )
    """)
    conn.commit()
    conn.close()


def upsert_rows(rows: list):
    if not rows:
        return
    conn = get_connection()
    cur = get_cursor(conn)
    p = placeholder()
    for row in rows:
        cur.execute(f"""
            INSERT INTO restaurant_stats
                (date, unit_id, unit_name, dine_in_orders, cooking_time_sum, tracking_pending_sum, orders_over_15min)
            VALUES ({p},{p},{p},{p},{p},{p},{p})
            ON CONFLICT (date, unit_id) DO UPDATE SET
                unit_name             = EXCLUDED.unit_name,
                dine_in_orders        = EXCLUDED.dine_in_orders,
                cooking_time_sum      = EXCLUDED.cooking_time_sum,
                tracking_pending_sum  = EXCLUDED.tracking_pending_sum,
                orders_over_15min     = EXCLUDED.orders_over_15min
        """, (
            row["date"], row["unit_id"], row["unit_name"],
            row["dine_in_orders"], row["cooking_time_sum"], row["tracking_pending_sum"], row["orders_over_15min"],
        ))
    conn.commit()
    conn.close()


def date_range(start: str, end: str):
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        yield str(d)
        d += timedelta(days=1)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date",   required=True)
    args = parser.parse_args()

    ensure_table()

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    units = [u for u in units if u.get("restaurant_open_date")]

    days = list(date_range(args.from_date, args.to_date))
    total_days = len(days)
    all_rows = []

    for i, day in enumerate(days, 1):
        print(f"\n[{i}/{total_days}] {day}", flush=True)
        headers = make_headers()
        for unit in units:
            row = fetch_day(unit["id"], unit["name"], day, headers)
            print(f"  {unit['name']}: {'ok' if row else 'skip'}", flush=True)
            if row:
                all_rows.append(row)

        if len(all_rows) >= 42:
            upsert_rows(all_rows)
            print(f"  upserted {len(all_rows)} rows", flush=True)
            all_rows = []

    if all_rows:
        upsert_rows(all_rows)
        print(f"  upserted {len(all_rows)} rows", flush=True)

    print("Done.")


if __name__ == "__main__":
    main()
