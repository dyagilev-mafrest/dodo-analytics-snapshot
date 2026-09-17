# Dodo IS -> PostgreSQL (Supabase): productivity_stats (per unit per day)
#
# Endpoint: production/productivity
# Scope required: productionefficiency
# Returns: laborHours, sales, productsPerLaborHour, salesPerLaborHour
# Params: units (comma-sep), from/to (hour-rounded datetime, e.g. 2026-06-22T00:00:00)
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


def api_get(path: str, params: dict, headers: dict, retries: int = 6) -> dict:
    url = f"{BASE}/{path}"
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=90)
            if r.status_code == 401:
                stale = headers.get("Authorization", "").removeprefix("Bearer ")
                headers["Authorization"] = f"Bearer {get_access_token(force_refresh=True, _stale_token=stale)}"
                continue
            if r.status_code == 429:
                if attempt == retries - 1:
                    r.raise_for_status()
                retry_after = float(r.headers.get("Retry-After", 0) or 0)
                wait = max(retry_after, 20 * (attempt + 1))
                print(f"  retry {attempt+1}/{retries}: 429 Too Many Requests, waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1}/{retries}: {e}")
            time.sleep(5 * (attempt + 1))
    return {}


class FetchError(Exception):
    """Raised when the API call itself failed (as opposed to legitimately empty data)."""


def fetch_day(unit_id: str, unit_name: str, day: str, headers: dict):
    """Fetch productivity for one unit for one day.

    API aggregates the given period; for daily granularity we call with
    from=dayT00:00:00, to=next_dayT00:00:00 (hour-rounded boundary required).

    Returns None when the API call succeeded but there is legitimately no data
    (e.g. labor_hours == 0). Raises FetchError when the API call itself failed,
    so callers can tell "no data" apart from "we don't know" and fail loudly
    instead of silently reporting success with gaps.
    """
    next_day = str(date.fromisoformat(day) + timedelta(days=1))
    try:
        data = api_get("production/productivity", {
            "units": unit_id,
            "from": f"{day}T00:00:00",
            "to": f"{next_day}T00:00:00",
        }, headers)
    except Exception as e:
        print(f"  ERROR {unit_name} {day}: {e}")
        raise FetchError(f"{unit_name} {day}: {e}") from e

    items = data.get("productivityStatistics", [])
    if not items:
        return None
    item = items[0]
    labor_hours = item.get("laborHours") or 0
    if labor_hours == 0:
        return None
    return {
        "date": day,
        "unit_id": unit_id,
        "unit_name": unit_name,
        "labor_hours": labor_hours,
        "sales": item.get("sales") or 0,
        "products_per_labor_hour": item.get("productsPerLaborHour") or 0,
        "sales_per_labor_hour": item.get("salesPerLaborHour") or 0,
        "avg_heated_shelf_time": item.get("avgHeatedShelfTime"),
        "orders_per_courier_hour": item.get("ordersPerCourierLabourHour"),
    }


def upsert_rows(rows: list):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO productivity_stats
            (date, unit_id, unit_name,
             labor_hours, sales,
             products_per_labor_hour, sales_per_labor_hour,
             avg_heated_shelf_time, orders_per_courier_hour)
        VALUES %s
        ON CONFLICT (date, unit_id) DO UPDATE SET
            unit_name               = EXCLUDED.unit_name,
            labor_hours             = EXCLUDED.labor_hours,
            sales                   = EXCLUDED.sales,
            products_per_labor_hour = EXCLUDED.products_per_labor_hour,
            sales_per_labor_hour    = EXCLUDED.sales_per_labor_hour,
            avg_heated_shelf_time   = EXCLUDED.avg_heated_shelf_time,
            orders_per_courier_hour = EXCLUDED.orders_per_courier_hour
    """, [
        (
            row["date"], row["unit_id"], row["unit_name"],
            row["labor_hours"], row["sales"],
            row["products_per_labor_hour"], row["sales_per_labor_hour"],
            row["avg_heated_shelf_time"], row["orders_per_courier_hour"],
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
    errors = []

    for i, day in enumerate(days, 1):
        print(f"\n[{i}/{total_days}] {day}", flush=True)
        headers = make_headers()
        for unit in units:
            try:
                row = fetch_day(unit["id"], unit["name"], day, headers)
            except FetchError as e:
                errors.append(str(e))
                continue
            print(f"  {unit['name']}: {'ok' if row else 'skip'}", flush=True)
            if row:
                all_rows.append(row)
            time.sleep(1)  # be gentle with the shared Dodo IS rate limit

        if len(all_rows) >= 49:
            upsert_rows(all_rows)
            print(f"  upserted {len(all_rows)} rows", flush=True)
            all_rows = []

    if all_rows:
        upsert_rows(all_rows)
        print(f"  upserted {len(all_rows)} rows", flush=True)

    if errors:
        print(f"\n{len(errors)} fetch(es) failed:")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit(1)

    print("Done.")


if __name__ == "__main__":
    main()
