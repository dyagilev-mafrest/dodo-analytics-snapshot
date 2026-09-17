# Dodo IS -> PostgreSQL (Supabase): handover_time_stats (order time decomposed
# by stage, per unit per day per sales channel).
#
# Endpoint: production/orders-handover-statistics — Dodo IS already aggregates
# this per-stage, no per-order join needed on our side (unlike delivery_stats'
# orders_over_40min / restaurant_stats' orders_over_15min, which do their own
# per-order handover-time join). One call per (unit, day, channel).
#
# Valid salesChannels values verified empirically: "Delivery", "DineIn",
# "TakeAway" — NOT "Dine-in"/"Takeaway"/"Restaurant" (those 400). avgOrderAssemblyTime
# is null for Delivery (stage doesn't apply) and populated for DineIn/TakeAway —
# a distinct stage, not the same field get_restaurant.py folds into cookingTime.
#
# No data before ~2024-01-15 (same underlying source as production/orders-
# handover-time).
import json, time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token
from db import get_connection, get_cursor

load_dotenv()
BASE = "https://api.dodois.io/dodopizza/ru"
UNITS_FILE = "yakutsk_units.json"
CHANNELS = ["Delivery", "DineIn", "TakeAway"]


def make_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}"}


def api_get(path: str, params: dict, headers: dict, retries: int = 3) -> dict:
    url = f"{BASE}/{path}"
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


def fetch_stats(unit_id: str, channel: str, day: str) -> dict | None:
    headers = make_headers()
    data = api_get("production/orders-handover-statistics", {
        "units": unit_id, "salesChannels": channel,
        "from": f"{day}T00:00:00", "to": f"{day}T23:59:59",
    }, headers)
    rows = data.get("ordersHandoverStatistics", [])
    return rows[0] if rows else None


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO handover_time_stats
            (date, unit_id, sales_channel, avg_tracking_pending_time, avg_cooking_time,
             avg_heated_shelf_time, avg_order_assembly_time, avg_order_handover_time, orders_count)
        VALUES %s
        ON CONFLICT (date, unit_id, sales_channel) DO UPDATE SET
            avg_tracking_pending_time = EXCLUDED.avg_tracking_pending_time,
            avg_cooking_time          = EXCLUDED.avg_cooking_time,
            avg_heated_shelf_time     = EXCLUDED.avg_heated_shelf_time,
            avg_order_assembly_time   = EXCLUDED.avg_order_assembly_time,
            avg_order_handover_time   = EXCLUDED.avg_order_handover_time,
            orders_count              = EXCLUDED.orders_count
    """, [
        (r["date"], r["unit_id"], r["sales_channel"], r["avg_tracking_pending_time"],
         r["avg_cooking_time"], r["avg_heated_shelf_time"], r["avg_order_assembly_time"],
         r["avg_order_handover_time"], r["orders_count"])
        for r in rows
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
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    days = list(date_range(args.from_date, args.to_date))
    total = len(days) * len(units) * len(CHANNELS)
    done = 0

    for day in days:
        print(f"\n[{day}]", flush=True)
        rows = []
        jobs = [(unit, channel) for unit in units for channel in CHANNELS]
        with ThreadPoolExecutor(max_workers=len(units)) as pool:
            futures = {pool.submit(fetch_stats, unit["id"], channel, day): (unit, channel) for unit, channel in jobs}
            for future in as_completed(futures):
                unit, channel = futures[future]
                stat = future.result()
                done += 1
                if stat is None:
                    print(f"  [{done}/{total}] {unit.get('name')} {channel}: no data", flush=True)
                    continue
                rows.append({
                    "date": day, "unit_id": unit["id"], "sales_channel": channel,
                    "avg_tracking_pending_time": stat.get("avgTrackingPendingTime"),
                    "avg_cooking_time": stat.get("avgCookingTime"),
                    "avg_heated_shelf_time": stat.get("avgHeatedShelfTime"),
                    "avg_order_assembly_time": stat.get("avgOrderAssemblyTime"),
                    "avg_order_handover_time": stat.get("avgOrderHandoverTime"),
                    "orders_count": stat.get("ordersCount", 0),
                })
                print(f"  [{done}/{total}] {unit.get('name')} {channel}: {stat.get('ordersCount', 0)} orders", flush=True)
        upsert_rows(rows)
        print(f"  upserted {len(rows)} rows", flush=True)

    print("Done.")


if __name__ == "__main__":
    main()
