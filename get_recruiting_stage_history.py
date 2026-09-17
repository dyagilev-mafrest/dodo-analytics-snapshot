# Bitrix24 CRM -> PostgreSQL (Supabase): recruiting_stage_events
#
# Companion to get_recruiting.py — recruiting_deals only stores each deal's
# CURRENT stage, so a "by week" chart built on it is a cohort view (see that
# file's comments). This script pulls the actual per-deal stage transition
# history via crm.stagehistory.list, giving one row per real transition event
# with an exact timestamp — lets the dashboard count "how many events
# happened in week X" instead of "where did week-X's cohort end up by today".
#
# entityTypeId=2 = deals. filter[@OWNER_ID][]=... accepts a batch of deal IDs
# per call (verified live 2026-08-06) — much cheaper than one call per deal.
# Standard Bitrix24 list pagination (50/page via `next`).
import time

import httpx
from dotenv import load_dotenv
import os

from db import get_connection, get_cursor

load_dotenv()
WEBHOOK = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
CATEGORY_ID = 2  # "HR" pipeline
ENTITY_TYPE_ID = 2  # deals
BATCH_SIZE = 50  # deal IDs per crm.stagehistory.list call


def api_get(method: str, params: dict, retries: int = 6) -> dict:
    url = f"{WEBHOOK}/{method}.json"
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, timeout=60)
            if r.status_code == 400 and "QUERY_LIMIT_EXCEEDED" in r.text:
                wait = 10 * (attempt + 1)
                print(f"  retry {attempt+1}/{retries}: rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"{data['error']}: {data.get('error_description')}")
            return data
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1}/{retries}: {e}")
            time.sleep(5 * (attempt + 1))
    return {}


def fetch_stage_map() -> dict[str, str]:
    """STATUS_ID (e.g. 'C2:UC_NOWQUB') -> stage name (e.g. '02. Недозвон')."""
    data = api_get("crm.dealcategory.stage.list", {"id": CATEGORY_ID})
    return {s["STATUS_ID"]: s["NAME"] for s in data["result"]}


def fetch_deal_ids(modified_since: str | None) -> list[int]:
    """deal_id list from recruiting_deals — the set to (re)fetch stage history for."""
    conn = get_connection()
    cur = get_cursor(conn)
    if modified_since:
        cur.execute("SELECT deal_id FROM recruiting_deals WHERE date_modify >= %s", (modified_since,))
    else:
        cur.execute("SELECT deal_id FROM recruiting_deals")
    ids = [row["deal_id"] for row in cur.fetchall()]
    conn.close()
    return ids


def fetch_stage_history(deal_ids: list[int]) -> list[dict]:
    all_items = []
    for i in range(0, len(deal_ids), BATCH_SIZE):
        batch = deal_ids[i:i + BATCH_SIZE]
        start = 0
        while True:
            params = {"entityTypeId": ENTITY_TYPE_ID, "start": start}
            for j, deal_id in enumerate(batch):
                params[f"filter[@OWNER_ID][{j}]"] = deal_id
            data = api_get("crm.stagehistory.list", params)
            items = data.get("result", {}).get("items", [])
            all_items.extend(items)
            nxt = data.get("next")
            if nxt is None:
                break
            start = nxt
        done = min(i + BATCH_SIZE, len(deal_ids))
        if done % 1000 < BATCH_SIZE:
            print(f"  {done}/{len(deal_ids)} deals processed, {len(all_items)} events so far", flush=True)
    return all_items


def transform(items: list[dict], stage_map: dict) -> list[dict]:
    return [{
        "event_id": int(item["ID"]),
        "deal_id": int(item["OWNER_ID"]),
        "stage_id": item["STAGE_ID"],
        "stage_name": stage_map.get(item["STAGE_ID"]),
        "created_time": item["CREATED_TIME"],
    } for item in items]


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO recruiting_stage_events (event_id, deal_id, stage_id, stage_name, created_time)
        VALUES %s
        ON CONFLICT (event_id) DO NOTHING
    """, [
        (r["event_id"], r["deal_id"], r["stage_id"], r["stage_name"], r["created_time"])
        for r in rows
    ], page_size=500)
    conn.commit()
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--modified-since", default=None,
                         help="ISO date/datetime — only (re)pull stage history for deals modified since then. Omit for full backfill.")
    args = parser.parse_args()

    print("Fetching stage names...", flush=True)
    stage_map = fetch_stage_map()

    print("Fetching deal IDs...", flush=True)
    deal_ids = fetch_deal_ids(args.modified_since)
    print(f"  {len(deal_ids)} deals to check", flush=True)

    print("Fetching stage history...", flush=True)
    items = fetch_stage_history(deal_ids)
    print(f"  {len(items)} stage events fetched", flush=True)

    rows = transform(items, stage_map)
    upsert_rows(rows)
    print(f"Upserted {len(rows)} rows into recruiting_stage_events")


if __name__ == "__main__":
    main()
