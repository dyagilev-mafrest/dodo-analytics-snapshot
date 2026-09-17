# Google Sheets -> PostgreSQL (Supabase): hr_hiring_funnel
#
# Source: HR meetings spreadsheet, sheet "HR meet 2026" — filled in weekly by
# HR during their team meeting. Independent of staffing_plan (migration 026,
# sheet "new", filled by pizzeria managers with headcount target/actual) —
# that tracks the staffing GAP, this tracks the response FUNNEL that's meant
# to close it (responses -> interviews scheduled/conducted -> trial shifts),
# which exists nowhere else (not in Bitrix recruiting_deals either).
#
# Auth: same Google service account as get_staffing_plan.py
# (GOOGLE_SERVICE_ACCOUNT_JSON) — already shared with this spreadsheet too.
#
# Sheet layout ("HR meet 2026" tab, columns A-L only — everything past L is
# out of scope): a vertically repeating block per week — a header row
# containing "D(D).M(M).YY - D(D).M(M).YY N неделя", then a column-header
# row, then 7 rows (one per position: Пиццамейкеры/Кассиры/Клинеры/
# Тестомейкеры/Повара МД/Курьеры/Кухня ИТОГО) with plan/fact in columns D/E/G.
#
# The response-funnel mini-table (columns H-L: "Всего откликов в HR" /
# "Отклики ин. граждан" / "Курьеры") is NOT row-aligned with the position
# rows above — it's visually crammed into the same 7-row block via merged
# cells, landing on whichever position row happens to share that row index
# (verified live 2026-08-07: lands on rows 1, 2, 4 of the block, which drifts
# relative to the position order if a block ever gains/loses a note row).
# So we match by CONTENT (column B for position, column H for funnel label),
# not by fixed row offset.
#
# Only "Всего откликов в HR" (kitchen-wide) and "Курьеры" (courier) funnel
# rows are loaded. "Отклики ин. граждан" is a breakdown of the kitchen total,
# not additive — out of scope. Couriers get hired straight to a trial shift
# without a formal interview (confirmed by user 2026-08-07), so interview
# columns are never populated for their row (literal "х" placeholder) and
# aren't stored for courier.
#
# "%вып-я" from the sheet is NOT loaded — it's often blank or #DIV/0! when
# the plan is 0; the dashboard recomputes it from fact/plan on the fly.
import json
import os
import re
from datetime import date, timedelta

import gspread
from dotenv import load_dotenv

from db import get_connection, get_cursor
from sheets_retry import retry_google

load_dotenv()
SPREADSHEET_ID = "1Qm-4sQLD0VKWz3nVDJ8FSA3FnhLbd-5GYkQ4MWQ2xIk"
SHEET_NAME = "HR meet 2026"

WEEK_HEADER_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2})\s*-\s*\d{1,2}\.\d{1,2}\.\d{2}\s*\d+\s*недел")

ERROR_VALUES = {"#DIV/0!", "#REF!", "#N/A", "#VALUE!", "#NAME?", "х", "x"}


def parse_int(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s or s in ERROR_VALUES:
        return None
    s = s.replace(",", ".").replace("\xa0", "").replace("%", "")
    try:
        return int(float(s))
    except ValueError:
        # Occasionally two systems' counts are jammed into one cell, e.g.
        # "214 (даталенс); 29 (битрикс)" — take the first number and flag it
        # rather than guess whether to sum two different tracking systems.
        m = re.search(r"\d+", s)
        if m:
            print(f"  [warn] non-numeric cell {raw!r} -> using first number {m.group()}")
            return int(m.group())
        print(f"  [warn] unparseable cell {raw!r} -> NULL")
        return None


def parse_week_start(cell: str) -> str | None:
    m = WEEK_HEADER_RE.search(cell or "")
    if not m:
        return None
    day, month, year = m.groups()
    return f"20{year}-{int(month):02d}-{int(day):02d}"


def fetch_rows() -> list[list[str]]:
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    gc = gspread.service_account_from_dict(json.loads(sa_json))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы")
    return retry_google(
        # До M, а не до L: «назначенные пробные смены» лежат в M, и прежняя
        # выборка их просто не запрашивала.
        lambda: sh.worksheet(SHEET_NAME).get_values("A1:M1000"),
        what=f"чтение листа «{SHEET_NAME}»",
    )


# Метки колонок блока. Ищем их в строке-заголовке КАЖДОГО блока, а не считаем
# смещения: в 38-й неделе 2026 в лист добавили «Уволено», и всё правее сдвинулось.
# Прежняя версия читала смещения жёстко и из-за этого брала «план на следующую
# неделю» из чужой колонки, а воронку — на одну левее.
COL_LABELS = {
    "plan": "План (предыдущей недели)",
    "fact": "Факт принятых",
    "dismissed": "Уволено",
    "plan_next": "План на следующую неделю",
    "responses": "Количество отликов",
    "scheduled": "Количество назначеных",
    "conducted": "Количество проведенных",
    "trials": "Количество назначенных",
}


def find_columns(row: list[str]) -> dict[str, int] | None:
    """Карта «роль -> индекс колонки» по строке-заголовку блока."""
    found: dict[str, int] = {}
    for idx, cell in enumerate(row):
        text = re.sub(r"\s+", " ", (cell or "").strip())
        for role, label in COL_LABELS.items():
            if role in found:
                continue
            if text.startswith(label):
                found[role] = idx
    # Заголовок опознаём по плану и факту: без них это не шапка блока.
    return found if "plan" in found and "fact" in found else None


def blank_week(week: str) -> dict:
    return {
        "week_start": week,
        "kitchen_hiring_plan": None, "kitchen_hired_fact": None, "kitchen_dismissed": None,
        "responses_total": None, "interviews_scheduled": None, "interviews_conducted": None,
        "trial_shifts": None,
        "courier_hiring_plan": None, "courier_hired_fact": None, "courier_dismissed": None,
        "courier_responses": None, "courier_trial_shifts": None,
    }


def shift_week(week: str, weeks: int) -> str:
    d = date.fromisoformat(week) + timedelta(days=7 * weeks)
    return d.isoformat()


def transform(rows: list[list[str]]) -> list[dict]:
    """Блок листа -> строки по ОТЧЁТНЫМ неделям.

    Блок заполняется на встрече в начале недели N и отчитывается за неделю N-1:
    заголовок так и говорит — «План предыдущей недели». Поэтому план, факт,
    увольнения и воронка из блока N ложатся под неделю N-1, а «план на следующую
    неделю» — под неделю N. Следующий блок подтвердит его своей колонкой «план
    предыдущей», так что числа сойдутся сами.
    """
    by_week: dict[str, dict] = {}
    cur_block: str | None = None
    cols: dict[str, int] = {}

    def week(w: str) -> dict:
        return by_week.setdefault(w, blank_week(w))

    for row in rows:
        row = row + [""] * (13 - len(row))

        header_week = parse_week_start(row[0])
        if header_week:
            cur_block = header_week
            cols = {}
            continue
        if cur_block is None:
            continue

        found = find_columns(row)
        if found:
            cols = found
            continue
        if not cols:
            continue

        reported = shift_week(cur_block, -1)
        cell = lambda role: parse_int(row[cols[role]]) if role in cols else None

        position = row[1].strip()
        if position in ("Кухня ИТОГО", "Курьеры"):
            prefix = "kitchen" if position == "Кухня ИТОГО" else "courier"
            r = week(reported)
            r[f"{prefix}_hiring_plan"] = cell("plan")
            r[f"{prefix}_hired_fact"] = cell("fact")
            r[f"{prefix}_dismissed"] = cell("dismissed")
            # План на следующую неделю — это план недели самого блока.
            nxt = cell("plan_next")
            if nxt is not None:
                week(cur_block)[f"{prefix}_hiring_plan"] = nxt

        label = re.sub(r"\s+", " ", (row[cols.get("responses", 8) - 1] or "").strip())
        if label == "Всего откликов в HR":
            r = week(reported)
            r["responses_total"] = cell("responses")
            r["interviews_scheduled"] = cell("scheduled")
            r["interviews_conducted"] = cell("conducted")
            r["trial_shifts"] = cell("trials")
        elif label == "Курьеры":
            r = week(reported)
            r["courier_responses"] = cell("responses")
            r["courier_trial_shifts"] = cell("trials")

    return [by_week[w] for w in sorted(by_week)]


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO hr_hiring_funnel
            (week_start, kitchen_hiring_plan, kitchen_hired_fact, kitchen_dismissed,
             responses_total, interviews_scheduled, interviews_conducted, trial_shifts,
             courier_hiring_plan, courier_hired_fact, courier_dismissed,
             courier_responses, courier_trial_shifts)
        VALUES %s
        ON CONFLICT (week_start) DO UPDATE SET
            kitchen_hiring_plan  = EXCLUDED.kitchen_hiring_plan,
            kitchen_hired_fact   = EXCLUDED.kitchen_hired_fact,
            kitchen_dismissed    = EXCLUDED.kitchen_dismissed,
            responses_total      = EXCLUDED.responses_total,
            interviews_scheduled = EXCLUDED.interviews_scheduled,
            interviews_conducted = EXCLUDED.interviews_conducted,
            trial_shifts         = EXCLUDED.trial_shifts,
            courier_hiring_plan  = EXCLUDED.courier_hiring_plan,
            courier_hired_fact   = EXCLUDED.courier_hired_fact,
            courier_dismissed    = EXCLUDED.courier_dismissed,
            courier_responses    = EXCLUDED.courier_responses,
            courier_trial_shifts = EXCLUDED.courier_trial_shifts
    """, [(
        r["week_start"], r["kitchen_hiring_plan"], r["kitchen_hired_fact"], r["kitchen_dismissed"],
        r["responses_total"], r["interviews_scheduled"], r["interviews_conducted"], r["trial_shifts"],
        r["courier_hiring_plan"], r["courier_hired_fact"], r["courier_dismissed"],
        r["courier_responses"], r["courier_trial_shifts"],
    ) for r in rows], page_size=200)
    conn.commit()
    conn.close()


def main():
    rows = transform(fetch_rows())
    print(f"Разобрано недель: {len(rows)} ({rows[0]['week_start']} - {rows[-1]['week_start']})")
    print("нед        кухня план/факт/уволено | курьеры план/факт/уволено | воронка откл/назн/пров/смены")
    for r in rows[-8:]:
        g = lambda k: "-" if r[k] is None else str(r[k])
        print(f'  {r["week_start"]}  {g("kitchen_hiring_plan"):>4} {g("kitchen_hired_fact"):>4} {g("kitchen_dismissed"):>4}'
              f'   | {g("courier_hiring_plan"):>4} {g("courier_hired_fact"):>4} {g("courier_dismissed"):>4}'
              f'   | {g("responses_total"):>5} {g("interviews_scheduled"):>4} {g("interviews_conducted"):>4} {g("trial_shifts"):>4}')
    if "--dry-run" in os.sys.argv:
        print("--dry-run: в БД не пишем.")
        return
    upsert_rows(rows)
    print(f"Загружено {len(rows)} недель в hr_hiring_funnel.")


if __name__ == "__main__":
    main()
