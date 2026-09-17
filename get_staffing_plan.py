# Google Sheets -> PostgreSQL (Supabase): staffing_plan
#
# Source: HR hiring-plan spreadsheet, sheet "new" — filled in weekly by each
# pizzeria's manager (headcount target/actual, forecast departures, hiring
# target per position), which HR then uses to build the recruiting plan.
# Independent of the Bitrix24 CRM data (get_recruiting.py /
# get_recruiting_stage_history.py) — that tracks the candidate pipeline
# itself, this tracks the staffing gap it's meant to close.
#
# Auth: Google service account (GOOGLE_SERVICE_ACCOUNT_JSON — full JSON key as
# a single-line env var, not a file, so the same value works locally and as a
# GitHub Actions secret). The org enforces iam.disableServiceAccountKeyCreation
# on its main GCP org, so this key was created under a personal (non-org)
# Google account and the sheet was shared with the service account's email.
#
# Sheet layout ("new" tab): a vertically repeating block per week — a header
# row somewhere containing "DD.MM.YY - DD.MM.YY - N неделя", followed by one
# row per unit (pizzeria/production site) until the next such header. Columns
# are wide-format: 4 positions (Пиццамейкер/Кассир/Клинер/Курьер) x 4 metrics
# each (цель команды/текущий штат/прогноз увольнений/цель найма), fixed at
# columns C-R (0-based indices 2-17) — verified live 2026-08-06. Stagers/
# medical-exam columns (U onward) are out of scope here. All units in the
# sheet are loaded (incl. ПРЦ/МД production sites and Якутск-10, a pizzeria
# under construction) — all part of MAFREST, not just the 7 pizzerias.
import json
import os
import re

import gspread
from dotenv import load_dotenv

from db import get_connection, get_cursor
from sheets_retry import retry_google

load_dotenv()
SPREADSHEET_ID = "1jLwYzuXHT7w_XkisJ0KenYVBrO2oagGMfZ9BY8RuZ6Q"
SHEET_NAME = "new"

# First date's year is sometimes missing in the source (e.g. row 52: "02.03 -
# 08.03.26 - 10 неделя") — fall back to the second date's year when so.
WEEK_HEADER_RE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2}))?\s*-\s*\d{1,2}\.\d{1,2}\.(\d{2})\s*-\s*\d+\s*недел")

# (position name, target_col, current_col, forecast_departures_col, hiring_target_col) — 0-based
POSITIONS = [
    ("Пиццамейкер", 2, 3, 4, 5),
    ("Кассир", 6, 7, 8, 9),
    ("Клинер", 10, 11, 12, 13),
    ("Курьер", 14, 15, 16, 17),
]

ERROR_VALUES = {"#DIV/0!", "#REF!", "#N/A", "#VALUE!", "#NAME?"}


def parse_int(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s or s in ERROR_VALUES:
        return None
    s = s.replace(",", ".").replace("\xa0", "")
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_week_start(cell: str) -> str | None:
    m = WEEK_HEADER_RE.search(cell or "")
    if not m:
        return None
    day, month, year1, year2 = m.groups()
    year = year1 or year2
    return f"20{year}-{int(month):02d}-{int(day):02d}"


def fetch_rows() -> list[list[str]]:
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    gc = gspread.service_account_from_dict(json.loads(sa_json))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы")
    return retry_google(
        lambda: sh.worksheet(SHEET_NAME).get_all_values(),
        what=f"чтение листа «{SHEET_NAME}»",
    )


def transform(rows: list[list[str]]) -> list[dict]:
    out = []
    week_start = None
    for row in rows:
        header_week = None
        for cell in row:
            header_week = parse_week_start(cell)
            if header_week:
                break
        if header_week:
            week_start = header_week
            continue

        unit_name = (row[0] if len(row) > 0 else "").strip()
        if not unit_name or week_start is None:
            continue

        for position, tgt_i, cur_i, dep_i, hire_i in POSITIONS:
            out.append({
                "week_start": week_start,
                "unit_name": unit_name,
                "position": position,
                "target_headcount": parse_int(row[tgt_i]) if tgt_i < len(row) else None,
                "current_headcount": parse_int(row[cur_i]) if cur_i < len(row) else None,
                "forecast_departures": parse_int(row[dep_i]) if dep_i < len(row) else None,
                "hiring_target": parse_int(row[hire_i]) if hire_i < len(row) else None,
            })
    return out


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO staffing_plan
            (week_start, unit_name, position, target_headcount, current_headcount,
             forecast_departures, hiring_target)
        VALUES %s
        ON CONFLICT (week_start, unit_name, position) DO UPDATE SET
            target_headcount    = EXCLUDED.target_headcount,
            current_headcount   = EXCLUDED.current_headcount,
            forecast_departures = EXCLUDED.forecast_departures,
            hiring_target       = EXCLUDED.hiring_target
    """, [
        (r["week_start"], r["unit_name"], r["position"], r["target_headcount"],
         r["current_headcount"], r["forecast_departures"], r["hiring_target"])
        for r in rows
    ], page_size=500)
    conn.commit()
    conn.close()


def main():
    print("Fetching sheet...", flush=True)
    rows = fetch_rows()
    print(f"  {len(rows)} raw rows fetched", flush=True)

    parsed = transform(rows)
    print(f"  {len(parsed)} (week, unit, position) rows parsed", flush=True)

    upsert_rows(parsed)
    print(f"Upserted {len(parsed)} rows into staffing_plan")


if __name__ == "__main__":
    main()
