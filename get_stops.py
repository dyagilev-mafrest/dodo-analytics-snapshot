# Dodo IS -> PostgreSQL (Supabase): stop_events (ingredient/channel/sector/product stops)
#
# Endpoints:
#   production/stop-sales-ingredients  -> event_type='ingredient'
#   production/stop-sales-channels     -> event_type='channel'
#   delivery/stop-sales-sectors        -> event_type='sector'
#   production/stop-sales-products     -> event_type='product'
#
# Product stops (~1300/day) are largely derived from ingredient stops (cascade),
# but we keep them as separate rows to compute lost revenue per product
# (see compute_lost_revenue.py) — entity_id carries productId/ingredientId for that.
# Data model: one row per stop event, upsert on id.
# The API returns active stops regardless of date range — upsert handles duplicates.
import os, json, time
from datetime import date, datetime, timedelta

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
            r = httpx.get(url, params=params, headers=headers, timeout=60)
            if r.status_code == 401:
                stale = headers.get("Authorization", "").removeprefix("Bearer ")
                headers["Authorization"] = f"Bearer {get_access_token(force_refresh=True, _stale_token=stale)}"
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError) as e:
            if attempt == retries - 1:
                raise
            wait = min(10 * (2 ** attempt), 120)
            print(f"  retry {attempt+1}/{retries} in {wait}s: {e}")
            time.sleep(wait)
    return {}


def dur_minutes(started: str | None, ended: str | None) -> float | None:
    if not started or not ended:
        return None
    s = datetime.fromisoformat(started)
    e = datetime.fromisoformat(ended)
    return round((e - s).total_seconds() / 60, 1)


def fetch_events(unit_ids: str, day: str, headers: dict) -> list[dict]:
    """Fetch all stop events that started on `day` (local time) for all units."""
    params = {
        "units": unit_ids,
        "from": f"{day}T00:00:00",
        "to":   f"{day}T23:59:59",
    }
    rows = []

    # Ingredients
    data = api_get("production/stop-sales-ingredients", params, headers)
    for item in data.get("stopSalesByIngredients", []):
        rows.append({
            "id":             item["id"],
            "event_type":     "ingredient",
            "unit_id":        item["unitId"],
            "unit_name":      item.get("unitName"),
            "entity_name":    item.get("ingredientName"),
            "entity_category":item.get("ingredientCategoryName"),
            "entity_id":      item.get("ingredientId"),
            "reason":         item.get("reason"),
            "started_at_local": item.get("startedAtLocal"),
            "ended_at_local":   item.get("endedAtLocal"),
            "duration_minutes": dur_minutes(item.get("startedAtLocal"), item.get("endedAtLocal")),
        })

    # Channels (full channel stops: Delivery / Dine-in / Takeaway)
    data = api_get("production/stop-sales-channels", params, headers)
    for item in data.get("stopSalesBySalesChannels", []):
        rows.append({
            "id":             item["id"],
            "event_type":     "channel",
            "unit_id":        item["unitId"],
            "unit_name":      item.get("unitName"),
            "entity_name":    item.get("salesChannelName"),
            "entity_category":item.get("channelStopType"),
            "entity_id":      None,
            "reason":         item.get("reason"),
            "started_at_local": item.get("startedAtLocal"),
            "ended_at_local":   item.get("endedAtLocal"),
            "duration_minutes": dur_minutes(item.get("startedAtLocal"), item.get("endedAtLocal")),
        })

    # Delivery sectors
    data = api_get("delivery/stop-sales-sectors", params, headers)
    for item in data.get("stopSalesBySectors", []):
        rows.append({
            "id":             item["id"].lower(),  # API returns uppercase, normalise
            "event_type":     "sector",
            "unit_id":        item["unitId"].lower(),
            "unit_name":      item.get("unitName"),
            "entity_name":    item.get("sectorName"),
            "entity_category":"subsector" if item.get("isSubSector") else "sector",
            "entity_id":      None,
            "reason":         None,
            "started_at_local": item.get("startedAtLocal"),
            "ended_at_local":   item.get("endedAtLocal"),
            "duration_minutes": dur_minutes(item.get("startedAtLocal"), item.get("endedAtLocal")),
        })

    # Products (cascades from ingredient stops; kept for per-product lost-revenue calc)
    data = api_get("production/stop-sales-products", params, headers)
    for item in data.get("stopSalesByProducts", []):
        rows.append({
            "id":             item["id"],
            "event_type":     "product",
            "unit_id":        item["unitId"],
            "unit_name":      item.get("unitName"),
            "entity_name":    item.get("productName"),
            "entity_category":item.get("productCategoryName"),
            "entity_id":      item.get("productId"),
            "reason":         item.get("reason"),
            "started_at_local": item.get("startedAtLocal"),
            "ended_at_local":   item.get("endedAtLocal"),
            "duration_minutes": dur_minutes(item.get("startedAtLocal"), item.get("endedAtLocal")),
        })

    # Filter to events that actually started on this day (API may return older active events)
    return [r for r in rows if r["started_at_local"] and r["started_at_local"][:10] == day]


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO stop_events
            (id, event_type, unit_id, unit_name,
             entity_name, entity_category, entity_id, reason,
             started_at_local, ended_at_local, duration_minutes)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET
            unit_name        = EXCLUDED.unit_name,
            entity_id        = EXCLUDED.entity_id,
            ended_at_local   = EXCLUDED.ended_at_local,
            duration_minutes = EXCLUDED.duration_minutes
    """, [
        (
            row["id"], row["event_type"], row["unit_id"], row["unit_name"],
            row["entity_name"], row["entity_category"], row["entity_id"], row["reason"],
            row["started_at_local"], row["ended_at_local"], row["duration_minutes"],
        )
        for row in rows
    ], page_size=100)
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
    unit_ids = ",".join(u["id"] for u in units)

    days = list(date_range(args.from_date, args.to_date))
    total = len(days)
    all_rows: list[dict] = []

    failed_days = []
    for i, day in enumerate(days, 1):
        print(f"\n[{i}/{total}] {day}", flush=True)
        try:
            headers = make_headers()
            rows = fetch_events(unit_ids, day, headers)
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError) as e:
            print(f"  SKIPPING {day} after exhausting retries: {e}", flush=True)
            failed_days.append(day)
            continue
        by_type = {}
        for r in rows:
            by_type[r["event_type"]] = by_type.get(r["event_type"], 0) + 1
        print(f"  {by_type}", flush=True)
        all_rows.extend(rows)

        if len(all_rows) >= 500:
            upsert_rows(all_rows)
            print(f"  upserted {len(all_rows)} rows", flush=True)
            all_rows = []

    if all_rows:
        upsert_rows(all_rows)
        print(f"  upserted {len(all_rows)} rows", flush=True)

    if failed_days:
        print(f"Done with {len(failed_days)} failed day(s): {failed_days}")
        raise SystemExit(1)

    print("Done.")


if __name__ == "__main__":
    main()
