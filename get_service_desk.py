# DodoDesk (NAUMEN ITSM365, sd/services/rest) -> PostgreSQL (Supabase): service_desk_tickets
#
# API notes (verified live 2026-08-13):
# - accessKey passed as a query param (not a header) — https://dodofranchaizing.itsm365.com/sd/services/rest
# - GET /get/{uuid} on serviceCall objects returns "Объект не найден" for this
#   API key (permission gap on that specific method), even though the UUID is
#   real — but GET /find/{fqn}/{filter}?attrs=... works fine and returns full
#   attribute sets. So this script uses /find exclusively.
# - /find has NO date-range or company filter (tried several JSON filter
#   shapes for registrationDate — from/to, gte/lte, array-as-interval — all
#   either errored or silently no-matched; NAUMEN docs don't document one
#   either). It also has no per-franchisee filter — the endpoint returns
#   serviceCall objects for the ENTIRE DodoDesk network (all franchisees
#   across Russia), not just ours.
# - So: page through /find with an empty filter ({}), and filter each page
#   client-side by clientOU.UUID against OUR_OUS. The list only grows at the
#   tail (new tickets get the next offset), so an incremental run just
#   resumes from the last saved offset — no date logic needed at all.
# - IMPORTANT (found 2026-08-14): the tail-append incremental sync above only
#   ever touches NEW tickets — it never revisits already-ingested ones, so a
#   ticket's state/mark/complianceSC (SLA) freezes at whatever it was when
#   first seen, even while still open in NAUMEN. Since /find can't filter by
#   UUID or date, the only way to refresh a SPECIFIC known ticket is to
#   re-fetch the same (offset, limit) page it was originally found on — the
#   list is append-only, so an item's page position is stable over time.
#   We now store that offset (network_offset) per ticket and, after the
#   normal append pass, re-fetch just the distinct pages that contain
#   currently-open tickets (see refresh_open_tickets()) — far cheaper than a
#   full network rescan (~6.5 min) while keeping open tickets' SLA current.
import sys
import time
from datetime import datetime, timedelta, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import httpx
from dotenv import load_dotenv
import os

from db import get_connection, get_cursor

load_dotenv()

BASE_URL = os.getenv("DODODESK_BASE_URL", "https://dodofranchaizing.itsm365.com/sd/services/rest")
ACCESS_KEY = os.environ["DODODESK_ACCESS_KEY"]
PAGE_SIZE = 1000
SYNC_KEY = "serviceCall"

# Дочерние подразделения компании "МАКАРОВ АНТОН" (organization = "ООО МАФРЕСТ")
# в DodoDesk — найдены через get/ou$2774025 (childOUs), верифицировано против
# реального дашборда DodoDesk 2026-08-13 (59 заявок за 03-09.08 -> у нас 58).
OUR_OUS = {
    "ou$2774026": "Якутск-1",
    "ou$2774027": "Якутск-2",
    "ou$2774028": "Якутск-3",
    "ou$2774029": "Якутск-4",
    "ou$2774030": "Якутск-5",
    "ou$2774031": "Якутск-6",
    "ou$5787301": "Якутск-7",
    "ou$5787302": "Якутск-ПРЦ",
    "ou$5787303": "Техническое отделение",
    "ou$5787305": "Склад РЦ",
    "ou$8264701": "Офис",
    "ou$14267610": "Дринкит-1",
    "ou$13220902": "Дринкит-2",
}

ATTRS = "UUID,title,shortDescr,state,mark,complianceSC,registrationDate,dateDecision,priority,service,clientOU,client,number,responsibleTeam,responsible,reactTime,reactionTime,reactOverdue,location"


def api_get(params: dict, retries: int = 5) -> list:
    url = f"{BASE_URL}/find/serviceCall/%7B%7D"
    for attempt in range(retries):
        try:
            r = httpx.get(url, params={**params, "accessKey": ACCESS_KEY}, timeout=60)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected response (not a list): {data!r:.500}")
            return data
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1}/{retries}: {e}")
            time.sleep(5 * (attempt + 1))
    return []


def fetch_page(offset: int, limit: int = PAGE_SIZE) -> list:
    return api_get({"offset": offset, "limit": limit, "attrs": ATTRS})


MOSCOW_TO_YAKUTSK_HOURS = 6  # оба региона без DST — сдвиг фиксированный, не зависит от сезона


def parse_naumen_date(s: str | None) -> str | None:
    """'2026.08.03 07:01:41' (московское время сервера NAUMEN — verified live
    2026-08-14 по распределению заявок по часам: почти ноль 22:00-04:00 МСК,
    пик 09:00-14:00 МСК, что после +6ч даёт классический рабочий график
    09:00-21:00 в Якутске) -> '2026-08-03 13:01:41' (Якутск, postgres-parseable).
    """
    if not s:
        return None
    moscow = datetime.strptime(s, "%Y.%m.%d %H:%M:%S")
    return (moscow + timedelta(hours=MOSCOW_TO_YAKUTSK_HOURS)).isoformat(sep=" ")


def transform(items: list[dict], page_offset: int) -> list[dict]:
    rows = []
    for item in items:
        ou = item.get("clientOU")
        if not ou or ou.get("UUID") not in OUR_OUS:
            continue
        mark = item.get("mark") or {}
        compliance = item.get("complianceSC") or {}
        priority = item.get("priority") or {}
        service = item.get("service") or {}
        client = item.get("client") or {}
        responsible_team = item.get("responsibleTeam") or {}
        responsible = item.get("responsible") or {}
        react_time = item.get("reactTime") or {}
        reaction_time = item.get("reactionTime") or {}
        react_overdue = item.get("reactOverdue") or {}
        location = item.get("location") or {}
        rows.append({
            "ticket_uuid": item["UUID"],
            "number": item.get("number"),
            "title": item["title"],
            "short_descr": item.get("shortDescr"),
            "responsible_team_title": responsible_team.get("title"),
            "responsible_title": responsible.get("title"),
            "location_title": location.get("title"),
            "network_offset": page_offset,
            "react_time_length": react_time.get("length"),
            "react_time_interval": react_time.get("interval"),
            "reaction_status": reaction_time.get("status"),
            "reaction_elapsed_ms": reaction_time.get("elapsed"),
            "react_overdue_elapsed_ms": react_overdue.get("elapsedFromOverdue"),
            "state": item["state"],
            "mark_code": mark.get("code"),
            "mark_title": mark.get("title"),
            "compliance_code": compliance.get("code"),
            "compliance_title": compliance.get("title"),
            "priority_code": priority.get("code"),
            "priority_title": priority.get("title"),
            "service_title": service.get("title"),
            "client_ou_uuid": ou["UUID"],
            "client_ou_title": OUR_OUS[ou["UUID"]],
            "client_name": client.get("title"),
            "registration_date": parse_naumen_date(item.get("registrationDate")),
            "date_decision": parse_naumen_date(item.get("dateDecision")),
        })
    return rows


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO service_desk_tickets
            (ticket_uuid, number, title, short_descr, responsible_team_title, responsible_title, location_title, network_offset,
             react_time_length, react_time_interval, reaction_status, reaction_elapsed_ms, react_overdue_elapsed_ms,
             state, mark_code, mark_title,
             compliance_code, compliance_title, priority_code, priority_title,
             service_title, client_ou_uuid, client_ou_title, client_name,
             registration_date, date_decision, loaded_at)
        VALUES %s
        ON CONFLICT (ticket_uuid) DO UPDATE SET
            title                    = EXCLUDED.title,
            short_descr              = EXCLUDED.short_descr,
            responsible_team_title   = EXCLUDED.responsible_team_title,
            responsible_title        = EXCLUDED.responsible_title,
            location_title           = EXCLUDED.location_title,
            network_offset           = EXCLUDED.network_offset,
            react_time_length        = EXCLUDED.react_time_length,
            react_time_interval      = EXCLUDED.react_time_interval,
            reaction_status          = EXCLUDED.reaction_status,
            reaction_elapsed_ms      = EXCLUDED.reaction_elapsed_ms,
            react_overdue_elapsed_ms = EXCLUDED.react_overdue_elapsed_ms,
            state                    = EXCLUDED.state,
            mark_code                = EXCLUDED.mark_code,
            mark_title               = EXCLUDED.mark_title,
            compliance_code          = EXCLUDED.compliance_code,
            compliance_title         = EXCLUDED.compliance_title,
            priority_code            = EXCLUDED.priority_code,
            priority_title           = EXCLUDED.priority_title,
            service_title            = EXCLUDED.service_title,
            client_ou_uuid           = EXCLUDED.client_ou_uuid,
            client_ou_title          = EXCLUDED.client_ou_title,
            client_name              = EXCLUDED.client_name,
            registration_date        = EXCLUDED.registration_date,
            date_decision            = EXCLUDED.date_decision,
            loaded_at                = EXCLUDED.loaded_at
    """, [
        (
            r["ticket_uuid"], r["number"], r["title"], r["short_descr"], r["responsible_team_title"], r["responsible_title"], r["location_title"], r["network_offset"],
            r["react_time_length"], r["react_time_interval"], r["reaction_status"], r["reaction_elapsed_ms"], r["react_overdue_elapsed_ms"],
            r["state"], r["mark_code"], r["mark_title"],
            r["compliance_code"], r["compliance_title"], r["priority_code"], r["priority_title"],
            r["service_title"], r["client_ou_uuid"], r["client_ou_title"], r["client_name"],
            r["registration_date"], r["date_decision"],
            datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds"),
        )
        for r in rows
    ], page_size=500)
    conn.commit()
    conn.close()


def get_watermark() -> int:
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT next_offset FROM service_desk_sync_state WHERE sync_key = %s", (SYNC_KEY,))
    row = cur.fetchone()
    conn.close()
    return row["next_offset"] if row else 0


def save_watermark(offset: int):
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        INSERT INTO service_desk_sync_state (sync_key, next_offset, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (sync_key) DO UPDATE SET
            next_offset = EXCLUDED.next_offset,
            updated_at  = EXCLUDED.updated_at
    """, (SYNC_KEY, offset, datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds")))
    conn.commit()
    conn.close()


def sync(start_offset: int, max_pages: int | None):
    offset = start_offset
    page = 0
    total_fetched = 0
    total_ours = 0

    while max_pages is None or page < max_pages:
        items = fetch_page(offset)
        if not items:
            print(f"  offset {offset}: пусто — конец списка")
            break

        rows = transform(items, offset)
        upsert_rows(rows)
        total_fetched += len(items)
        total_ours += len(rows)
        page += 1
        offset += len(items)
        print(f"  [page {page}] offset {offset - len(items)}: +{len(items)} заявок ({len(rows)} наши), сохранено", flush=True)

        save_watermark(offset)

        if len(items) < PAGE_SIZE:
            print(f"  offset {offset}: страница неполная — конец списка")
            break

    print(f"Готово. Всего просмотрено {total_fetched} заявок по всей сети DodoDesk, из них наших (МАФРЕСТ): {total_ours}.")


DONE_STATES = ("closed", "resolved")


def get_open_ticket_page_offsets() -> list[int]:
    """Уникальные network_offset среди наших открытых заявок — те самые
    страницы, которые нужно перечитать, чтобы обновить state/mark/SLA."""
    conn = get_connection()
    cur = get_cursor(conn)
    placeholders = ",".join(["%s"] * len(DONE_STATES))
    cur.execute(
        f"""
        SELECT DISTINCT network_offset FROM service_desk_tickets
        WHERE network_offset IS NOT NULL AND state NOT IN ({placeholders})
        ORDER BY network_offset
        """,
        DONE_STATES,
    )
    offsets = [row["network_offset"] for row in cur.fetchall()]
    conn.close()
    return offsets


def refresh_open_tickets():
    """Точечно перечитывает только те страницы /find, где лежат уже известные
    ОТКРЫТЫЕ заявки — обновляет их state/mark/complianceSC (SLA), не трогая
    закрытые/выполненные и не пересканируя всю сеть."""
    offsets = get_open_ticket_page_offsets()
    if not offsets:
        print("Обновление открытых заявок: нечего обновлять (нет открытых заявок с известным network_offset).")
        return

    print(f"Обновление открытых заявок: {len(offsets)} страниц(а) к перечитыванию...")
    total_ours = 0
    for i, page_offset in enumerate(offsets, start=1):
        items = fetch_page(page_offset)
        rows = transform(items, page_offset)
        upsert_rows(rows)
        total_ours += len(rows)
        print(f"  [{i}/{len(offsets)}] offset {page_offset}: {len(rows)} наши обновлены", flush=True)
    print(f"Обновление открытых заявок готово: {total_ours} записей обновлено на {len(offsets)} страницах.")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-backfill", action="store_true",
                         help="Игнорировать сохранённый watermark и начать с offset=0 (полная перезаливка истории)")
    parser.add_argument("--max-pages", type=int, default=None,
                         help=f"Ограничить число страниц за запуск (страница = {PAGE_SIZE} заявок по всей сети). Без ограничения — до конца списка.")
    parser.add_argument("--start-offset", type=int, default=None,
                         help="Явный стартовый offset для первого запуска (например, чтобы не тянуть всю историю сети с 2024 года). Игнорируется, если watermark уже сохранён — используйте --full-backfill, чтобы его переопределить.")
    parser.add_argument("--skip-refresh", action="store_true",
                         help="Не обновлять уже известные открытые заявки после обычной синхронизации (по умолчанию обновляются всегда, кроме --full-backfill, где это избыточно).")
    args = parser.parse_args()

    if args.full_backfill:
        start_offset = 0
    elif args.start_offset is not None and get_watermark() == 0:
        start_offset = args.start_offset
    else:
        start_offset = get_watermark()
    print(f"Стартуем с offset={start_offset} ({'полный backfill' if args.full_backfill else 'инкрементально'})")
    sync(start_offset, args.max_pages)

    if not args.full_backfill and not args.skip_refresh:
        refresh_open_tickets()


if __name__ == "__main__":
    main()
