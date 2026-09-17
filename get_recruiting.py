# Bitrix24 CRM -> PostgreSQL (Supabase): recruiting_deals
#
# Source: crm.deal.list, CATEGORY_ID=2 ("HR" pipeline) — one deal per
# candidate per vacancy. Auth is a plain incoming webhook (BITRIX_WEBHOOK_URL,
# e.g. https://<domain>.bitrix24.ru/rest/<user_id>/<token>) — no OAuth/token
# refresh needed, but the webhook's access is scoped to whatever its creating
# user can see in the UI (a non-admin webhook gets ACCESS_DENIED on metadata
# methods like crm.type.list/crm.status.list, even with scope=crm granted —
# this is a per-user permission issue, not a webhook-scope issue).
#
# Custom field IDs (verified live 2026-08-04, human-readable via
# crm.deal.userfield.get — crm.deal.userfield.list does NOT include labels,
# despite documentation suggesting otherwise):
#   UF_CRM_1698322013        Пиццерии       (enum: Якутск-1..7, Якутск-10, ПРЦ —
#                                            not filtered to our 7 units, see
#                                            migration 024's note)
#   UF_CRM_1698321689         Позиция        (enum: Пиццамейкер/Кассир/Курьер/...)
#   UF_CRM_1710756792942      Тип позиции    (enum: Курьер/Кухня/Склад)
#   UF_CRM_1698319434         Источник       (enum: HH.ru/Avito/...)
#   UF_CRM_1710756743839      Тип поиска     (enum: Отклик/Холодный поиск)
# These field IDs are specific to this Bitrix24 portal's field creation
# history — don't assume they're stable across other instances.
#
# Stage names come from crm.dealcategory.stage.list?id=2, not hardcoded, since
# stage sets can be edited in the Bitrix24 UI.
import json
import time

import httpx
from dotenv import load_dotenv
import os

from db import get_connection, get_cursor

load_dotenv()
WEBHOOK = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
CATEGORY_ID = 2  # "HR" pipeline

UF_PIZZERIA = "UF_CRM_1698322013"
UF_POSITION = "UF_CRM_1698321689"
UF_POSITION_TYPE = "UF_CRM_1710756792942"
UF_SOURCE = "UF_CRM_1698319434"
UF_SEARCH_TYPE = "UF_CRM_1710756743839"
ENUM_FIELDS = [UF_PIZZERIA, UF_POSITION, UF_POSITION_TYPE, UF_SOURCE, UF_SEARCH_TYPE]


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


def fetch_enum_maps() -> dict[str, dict[str, str]]:
    """field code -> {option id (str) -> option label}."""
    fields_data = api_get("crm.deal.userfield.list", {})
    field_ids = {f["FIELD_NAME"]: f["ID"] for f in fields_data["result"] if f["FIELD_NAME"] in ENUM_FIELDS}

    maps = {}
    for code, fid in field_ids.items():
        detail = api_get("crm.deal.userfield.get", {"id": fid})["result"]
        maps[code] = {item["ID"]: item["VALUE"] for item in detail.get("LIST", [])}
    return maps


def fetch_deals(modified_since: str | None = None) -> list[dict]:
    """Paginates crm.deal.list (50/page via `next`), CATEGORY_ID=2 only."""
    select = [
        "ID", "TITLE", "STAGE_ID", "DATE_CREATE", "DATE_MODIFY", "CLOSEDATE",
        "ASSIGNED_BY_ID",
    ] + ENUM_FIELDS

    filt = {"CATEGORY_ID": CATEGORY_ID}
    if modified_since:
        filt[">=DATE_MODIFY"] = modified_since

    all_deals = []
    start = 0
    while True:
        params = {"select[]": select, "order[ID]": "ASC", "start": start}
        for k, v in filt.items():
            params[f"filter[{k}]"] = v
        data = api_get("crm.deal.list", params)
        batch = data.get("result", [])
        all_deals.extend(batch)
        nxt = data.get("next")
        if nxt is None:
            break
        start = nxt
        if len(all_deals) % 1000 == 0:
            print(f"  fetched {len(all_deals)}/{data.get('total')}", flush=True)
    return all_deals


def transform(deals: list[dict], stage_map: dict, enum_maps: dict) -> list[dict]:
    def decode(code: str, raw_id) -> str | None:
        if not raw_id:
            return None
        return enum_maps.get(code, {}).get(str(raw_id))

    rows = []
    for d in deals:
        rows.append({
            "deal_id": int(d["ID"]),
            "candidate_name": d.get("TITLE"),
            "stage_id": d["STAGE_ID"],
            "stage_name": stage_map.get(d["STAGE_ID"]),
            "unit_name": decode(UF_PIZZERIA, d.get(UF_PIZZERIA)),
            "position": decode(UF_POSITION, d.get(UF_POSITION)),
            "position_type": decode(UF_POSITION_TYPE, d.get(UF_POSITION_TYPE)),
            "source": decode(UF_SOURCE, d.get(UF_SOURCE)),
            "search_type": decode(UF_SEARCH_TYPE, d.get(UF_SEARCH_TYPE)),
            "date_create": d["DATE_CREATE"],
            "date_modify": d.get("DATE_MODIFY"),
            "closedate": d.get("CLOSEDATE"),
            "assigned_by_id": d.get("ASSIGNED_BY_ID"),
        })
    return rows


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO recruiting_deals
            (deal_id, candidate_name, stage_id, stage_name, unit_name, position,
             position_type, source, search_type, date_create, date_modify,
             closedate, assigned_by_id)
        VALUES %s
        ON CONFLICT (deal_id) DO UPDATE SET
            candidate_name = EXCLUDED.candidate_name,
            stage_id       = EXCLUDED.stage_id,
            stage_name     = EXCLUDED.stage_name,
            unit_name      = EXCLUDED.unit_name,
            position       = EXCLUDED.position,
            position_type  = EXCLUDED.position_type,
            source         = EXCLUDED.source,
            search_type    = EXCLUDED.search_type,
            date_modify    = EXCLUDED.date_modify,
            closedate      = EXCLUDED.closedate,
            assigned_by_id = EXCLUDED.assigned_by_id
    """, [
        (r["deal_id"], r["candidate_name"], r["stage_id"], r["stage_name"],
         r["unit_name"], r["position"], r["position_type"], r["source"],
         r["search_type"], r["date_create"], r["date_modify"], r["closedate"],
         r["assigned_by_id"])
        for r in rows
    ], page_size=500)
    conn.commit()
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--modified-since", default=None,
                         help="ISO date/datetime — only pull deals modified since then (daily incremental run). Omit for full backfill.")
    args = parser.parse_args()

    print("Fetching stage names...", flush=True)
    stage_map = fetch_stage_map()
    print("Fetching enum field option maps...", flush=True)
    enum_maps = fetch_enum_maps()

    print("Fetching deals...", flush=True)
    deals = fetch_deals(args.modified_since)
    print(f"  {len(deals)} deals fetched", flush=True)

    rows = transform(deals, stage_map, enum_maps)
    upsert_rows(rows)
    print(f"Upserted {len(rows)} rows into recruiting_deals")


if __name__ == "__main__":
    main()
