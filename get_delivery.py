# Dodo IS -> PostgreSQL (Supabase): delivery_stats (per unit per day)
#
# Endpoints used:
#   delivery/statistics              - avg times, sales, courier shift duration
#   delivery/couriers-orders         - paginated order-level data for 1-in-1/2-in-1/3-in-1
#   delivery/vouchers                - late delivery certificates count
#   production/orders-handover-time  - full cooking time per order (joined by orderId)
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


def fetch_statistics(unit_id: str, day: str, headers: dict) -> dict:
    data = api_get("delivery/statistics", {
        "units": unit_id,
        "from": f"{day}T00:00:00",
        "to": f"{day}T23:59:59",
    }, headers)
    items = data.get("unitsStatistics", [])
    return items[0] if items else {}


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


def delivery_cooking_time_sum(hot_orders: list) -> int:
    """Sum of pure cookingTime for Delivery orders — same field used by restaurant_stats for dine-in."""
    return sum(
        (o.get("cookingTime") or 0)
        for o in hot_orders
        if o.get("salesChannel") == "Delivery"
    )


def aggregate_trip_orders(co_orders: list, hot_orders: list) -> tuple:
    # Full delivery time = trackingPendingTime + cookingTime + heatedShelfTime
    # (from orders-handover-time) + deliveryTime (from couriers-orders), joined by orderId.
    #
    # ~10% of couriers-orders have NO matching handover-time record at all
    # (verified: genuinely absent from that endpoint under any sales channel,
    # not a join bug — worse for some units, up to ~20%). The old fallback
    # formula for those (orderAssemblyAvgTime + heatedShelfTime + deliveryTime)
    # silently omitted cookingTime (~11 min average) entirely, undercounting
    # "over 40 min" for that slice by half (13.8% vs 25.2% for matched orders
    # with the accurate formula — verified against a real Dodo IS dashboard
    # screenshot, reference 25.7%). There's no reliable substitute for the
    # missing cookingTime, so unmatched orders are now excluded from
    # orders_over_40min's numerator AND denominator — callers must use
    # `matched` (not c1+c2+c3) as the denominator for that percentage.
    hot_map = {
        o["orderId"]: o
        for o in hot_orders
        if o.get("salesChannel") == "Delivery"
    }

    c1, c2, c3, late_40, late_60, matched = 0, 0, 0, 0, 0, 0
    for o in co_orders:
        if o.get("isFalseDelivery"):
            continue
        n = o.get("tripOrdersCount", 1)
        if n == 1:
            c1 += 1
        elif n == 2:
            c2 += 1
        else:
            c3 += 1

        hot = hot_map.get(o.get("orderId"))
        if not hot:
            continue
        matched += 1
        total_sec = (
            (hot.get("trackingPendingTime") or 0) +
            (hot.get("cookingTime") or 0) +
            (hot.get("heatedShelfTime") or 0) +
            (o.get("deliveryTime") or 0)
        )
        if total_sec > 2400:
            late_40 += 1
        if total_sec > 3600:
            late_60 += 1
    return c1, c2, c3, late_40, late_60, matched


# Кэш handover-заказов по (unit_id, day): при последовательном проходе дней
# каждые сутки нужны дважды — как свои и как «следующие» для предыдущего дня
# (см. fetch_handover_window). Без кэша это удвоило бы число запросов.
_hot_cache: dict[tuple[str, str], list] = {}


def fetch_handover_window(unit_id: str, day: str, headers: dict) -> list:
    """handover-заказы за `day` И за следующие сутки.

    Зачем окно шире запрошенных суток: `delivery/couriers-orders` нарезает день
    по UTC, а `production/orders-handover-time` — по локальному времени. Якутск
    это UTC+9, поэтому заказы, выполненные с 00:00 до 08:59 по-местному, у
    первого эндпоинта лежат в UTC-сутках предыдущего дня, а у второго — в
    локальных сутках следующего, и join по orderId их не находил.

    У пиццерий, закрытых ночью, в этом окне заказов нет — потеря 2-3%. У
    круглосуточных (Якутск-5, Якутск-6) там девять рабочих часов: пропадало
    16-21% заказов КАЖДУЮ неделю (проверено на восьми неделях 13.07–06.09.2026),
    и доля > 40 мин по ним врала на ±2,5 п.п. в обе стороны.

    Проверка фикса на неделе 31.08–06.09: покрытие 84,7% → 99,9%, ошибка доли
    у Якутск-5 +2,47 → +0,32 п.п., у Якутск-6 −1,09 → +0,19 п.п. Все 62
    непарных заказа одного тестового дня нашлись ровно в handover следующих
    суток — ни одного действительно отсутствующего.
    """
    from datetime import date as _date, timedelta as _td
    y, m, d = map(int, day.split("-"))
    nxt = (_date(y, m, d) + _td(days=1)).isoformat()
    return fetch_handover_day(unit_id, day, headers) + fetch_handover_day(unit_id, nxt, headers)


def fetch_handover_day(unit_id: str, day: str, headers: dict) -> list:
    """handover-заказы ровно за одни сутки, с кэшем."""
    key = (unit_id, day)
    if key not in _hot_cache:
        _hot_cache[key] = fetch_all_pages(
            "production/orders-handover-time", "ordersHandoverTime", unit_id, day, headers
        )
        # Кэш не должен расти бесконечно на длинном бэкфилле.
        if len(_hot_cache) > 32:
            for stale in list(_hot_cache)[:16]:
                del _hot_cache[stale]
    return _hot_cache[key]


def fetch_day(unit_id: str, unit_name: str, day: str, headers: dict):
    try:
        stats = fetch_statistics(unit_id, day, headers)
        if not stats:
            return None

        courier_orders = fetch_all_pages(
            "delivery/couriers-orders", "couriersOrders", unit_id, day, headers
        )
        # Для join'а окно шире суток, а для суммы времени готовки — строго свои
        # сутки: delivery_cooking_time_sum складывает по списку, и на широком
        # окне значение удвоилось бы.
        hot_orders = fetch_handover_window(unit_id, day, headers)
        hot_orders_day = fetch_handover_day(unit_id, day, headers)
        vouchers = fetch_all_pages(
            "delivery/vouchers", "vouchers", unit_id, day, headers
        )

        c1, c2, c3, late_40, late_60, matched = aggregate_trip_orders(courier_orders, hot_orders)
        cook_sum = delivery_cooking_time_sum(hot_orders_day)

        return {
            "date": day,
            "unit_id": unit_id,
            "unit_name": unit_name,
            "delivery_cooking_time_sum": cook_sum,
            "avg_fulfillment_time": stats.get("avgDeliveryOrderFulfillmentTime"),
            "avg_cooking_time":     stats.get("avgCookingTime"),
            "avg_shelf_time":       stats.get("avgHeatedShelfTime"),
            "avg_trip_time":        stats.get("avgOrderTripTime"),
            "delivery_orders":      stats.get("deliveryOrdersCount"),
            "delivery_sales":       stats.get("deliverySales"),
            "trips_count":          stats.get("tripsCount"),
            "trips_duration":       stats.get("tripsDuration"),
            "courier_shifts_duration": stats.get("couriersShiftsDuration"),
            "late_orders_count":    stats.get("lateOrdersCount"),
            "orders_1in1": c1,
            "orders_2in1": c2,
            "orders_3in1": c3,
            "certificates_count": len(vouchers),
            "orders_over_40min": late_40,
            "orders_over_60min": late_60,
            "orders_with_handover_data": matched,
        }
    except Exception as e:
        print(f"  ERROR {unit_name} {day}: {e}")
        return None


def upsert_rows(rows: list):
    if not rows:
        return
    conn = get_connection()
    cur = get_cursor(conn)
    p = placeholder()
    for row in rows:
        cur.execute(f"""
            INSERT INTO delivery_stats
                (date, unit_id, unit_name,
                 delivery_cooking_time_sum,
                 avg_fulfillment_time, avg_cooking_time, avg_shelf_time, avg_trip_time,
                 delivery_orders, delivery_sales,
                 trips_count, trips_duration, courier_shifts_duration,
                 late_orders_count,
                 orders_1in1, orders_2in1, orders_3in1,
                 certificates_count, orders_over_40min, orders_over_60min, orders_with_handover_data)
            VALUES
                ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
            ON CONFLICT (date, unit_id) DO UPDATE SET
                unit_name                  = EXCLUDED.unit_name,
                delivery_cooking_time_sum  = EXCLUDED.delivery_cooking_time_sum,
                avg_fulfillment_time       = EXCLUDED.avg_fulfillment_time,
                avg_cooking_time           = EXCLUDED.avg_cooking_time,
                avg_shelf_time             = EXCLUDED.avg_shelf_time,
                avg_trip_time              = EXCLUDED.avg_trip_time,
                delivery_orders            = EXCLUDED.delivery_orders,
                delivery_sales             = EXCLUDED.delivery_sales,
                trips_count                = EXCLUDED.trips_count,
                trips_duration             = EXCLUDED.trips_duration,
                courier_shifts_duration    = EXCLUDED.courier_shifts_duration,
                late_orders_count          = EXCLUDED.late_orders_count,
                orders_1in1                = EXCLUDED.orders_1in1,
                orders_2in1                = EXCLUDED.orders_2in1,
                orders_3in1                = EXCLUDED.orders_3in1,
                certificates_count         = EXCLUDED.certificates_count,
                orders_over_40min          = EXCLUDED.orders_over_40min,
                orders_over_60min          = EXCLUDED.orders_over_60min,
                orders_with_handover_data  = EXCLUDED.orders_with_handover_data
        """, (
            row["date"], row["unit_id"], row["unit_name"],
            row["delivery_cooking_time_sum"],
            row["avg_fulfillment_time"], row["avg_cooking_time"],
            row["avg_shelf_time"], row["avg_trip_time"],
            row["delivery_orders"], row["delivery_sales"],
            row["trips_count"], row["trips_duration"], row["courier_shifts_duration"],
            row["late_orders_count"],
            row["orders_1in1"], row["orders_2in1"], row["orders_3in1"],
            row["certificates_count"], row["orders_over_40min"], row["orders_over_60min"],
            row["orders_with_handover_data"],
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

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

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

        if len(all_rows) >= 49:
            upsert_rows(all_rows)
            print(f"  upserted {len(all_rows)} rows", flush=True)
            all_rows = []

    if all_rows:
        upsert_rows(all_rows)
        print(f"  upserted {len(all_rows)} rows", flush=True)

    print("Done.")


if __name__ == "__main__":
    main()
