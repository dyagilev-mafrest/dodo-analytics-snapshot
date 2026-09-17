# Dodo IS -> PostgreSQL (Supabase): hourly_revenue_by_sector (per unit per sector per hour)
#
# Endpoints used:
#   delivery/couriers-orders  - per-order data, has sectorId/sectorName but no revenue
#   accounting/sales          - per-order revenue (priceWithDiscount) + soldAtLocal, no sector
#
# No single endpoint carries both sector and revenue for an order, so this joins the
# two by orderId (~98% of couriers-orders find a match in accounting/sales — same
# order of unmatched-rate as delivery_stats' own handover-time join). Only Delivery-
# channel orders matter here (couriers only carry delivery orders anyway).
#
# Feeds the "Сектор" half of the Dodo IS lost-revenue formula (see
# compute_stop_lost_revenue.py): stop-time × average revenue in the same hour on the
# same weekday over the preceding 4 weeks, computed per sector.
import os, json, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from collections import defaultdict

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token
from db import get_connection, get_cursor

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


def fetch_couriers_orders(unit_id: str, day: str, headers: dict) -> list[dict]:
    items, skip, take = [], 0, 1000
    while True:
        data = api_get("delivery/couriers-orders", {
            "units": unit_id, "from": f"{day}T00:00:00", "to": f"{day}T23:59:59",
            "skip": skip, "take": take,
        }, headers)
        batch = data.get("couriersOrders", [])
        items.extend(batch)
        if data.get("isEndOfListReached", True):
            break
        skip += take
    return items


def fetch_sales(unit_id: str, day: str, headers: dict) -> list[dict]:
    orders, skip, take = [], 0, 1000
    while True:
        data = api_get("accounting/sales", {
            "units": unit_id, "from": f"{day}T00:00:00", "to": f"{day}T23:59:59",
            "skip": skip, "take": take,
        }, headers)
        batch = data.get("sales", [])
        orders.extend(batch)
        if data.get("isEndOfListReached", True):
            break
        skip += take
    return orders


def aggregate_hourly_sector_revenue(couriers_orders: list[dict], sales: list[dict], day: str, unit_id: str) -> list[dict]:
    revenue_by_order: dict[str, float] = {}
    hour_by_order: dict[str, int] = {}
    for o in sales:
        if o.get("salesChannel") != "Delivery":
            continue
        sold_at = o.get("soldAtLocal") or ""
        if len(sold_at) < 13:
            continue
        revenue_by_order[o["orderId"]] = sum(p.get("priceWithDiscount") or 0 for p in o.get("products", []))
        hour_by_order[o["orderId"]] = int(sold_at[11:13])

    agg: dict[tuple[int, str, str], dict] = defaultdict(lambda: {"revenue": 0.0, "orders_count": 0, "name": None})
    for co in couriers_orders:
        order_id = co.get("orderId")
        hour = hour_by_order.get(order_id)
        if hour is None:
            continue  # no matching accounting/sales row for this order
        sector_id = co.get("sectorId")
        if not sector_id:
            continue
        row = agg[(hour, unit_id, sector_id)]
        row["revenue"] += revenue_by_order[order_id]
        row["orders_count"] += 1
        row["name"] = co.get("sectorName")

    return [
        {
            "date": day, "hour": hour, "unit_id": unit_id,
            "sector_id": sector_id, "sector_name": v["name"],
            "revenue": v["revenue"], "orders_count": v["orders_count"],
        }
        for (hour, unit_id, sector_id), v in agg.items()
    ]


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO hourly_revenue_by_sector
            (date, hour, unit_id, sector_id, sector_name, revenue, orders_count)
        VALUES %s
        ON CONFLICT (date, hour, unit_id, sector_id) DO UPDATE SET
            sector_name  = EXCLUDED.sector_name,
            revenue      = EXCLUDED.revenue,
            orders_count = EXCLUDED.orders_count
    """, [
        (row["date"], row["hour"], row["unit_id"], row["sector_id"],
         row["sector_name"], row["revenue"], row["orders_count"])
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


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date",   required=True)
    args = parser.parse_args()

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    days = list(date_range(args.from_date, args.to_date))
    total = len(days) * len(units)
    done = 0
    done_lock = threading.Lock()

    def fetch_unit(day: str, unit: dict):
        # Each thread gets its own headers/token lookup — get_access_token() is
        # protected by _token_lock (get_sales.py), so this is safe to call concurrently.
        headers = make_headers()
        co = fetch_couriers_orders(unit["id"], day, headers)
        sales = fetch_sales(unit["id"], day, headers)
        rows = aggregate_hourly_sector_revenue(co, sales, day, unit["id"])
        return unit, co, sales, rows

    for day in days:
        print(f"\n[{day}]", flush=True)
        all_rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=len(units)) as pool:
            futures = [pool.submit(fetch_unit, day, unit) for unit in units]
            for future in as_completed(futures):
                unit, co, sales, rows = future.result()
                all_rows.extend(rows)
                with done_lock:
                    done += 1
                    print(f"  [{done}/{total}] {unit.get('name')}: {len(co)} courier orders, {len(sales)} sales -> {len(rows)} sector-hour rows", flush=True)
        upsert_rows(all_rows)
        print(f"  upserted {len(all_rows)} rows", flush=True)

    print("Done.")


if __name__ == "__main__":
    main()
