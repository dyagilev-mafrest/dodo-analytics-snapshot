# Google Sheets -> PostgreSQL (Supabase): leaders_protocol_metrics
#
# Source: "Протокол встречи лидеров Додо Якутск" — еженедельный протокол
# совета лидеров. Один лист на неделю (вкладка = дата встречи, "YYYY-MM-DD",
# встреча по средам — заполняется во вторник перед ней, смотрит показатели
# ПРОШЕДШЕЙ недели). Тянем только САМЫЙ СВЕЖИЙ лист при каждом запуске —
# это карточка "сейчас", не история/тренд (см. обсуждение с пользователем
# 2026-08-14: нужны только текущие цифры вверху дашборда, без сравнения
# с прошлыми неделями).
#
# Auth: тот же Google-сервисный аккаунт, что и у get_staffing_plan.py
# (GOOGLE_SERVICE_ACCOUNT_JSON) — добавлен пользователем в читатели таблицы.
#
# Блок "ГЛАВНЫЕ МЕТРИКИ КОМПАНИИ" — фиксированная раскладка (проверено на
# 4 последних неделях 2026-07-22..2026-08-12, полностью совпадает):
#   строка с заголовком "1.  ГЛАВНЫЕ МЕТРИКИ КОМПАНИИ" -> следующая строка
#   ("Статистика", ...) — заголовки колонок -> далее одна строка на метрику
#   (сейчас только "Выручка"), до первой строки с пустой колонкой B (label).
# Колонки (0-based): B=label, D=план(пред.неделя), F=факт(неделя),
# G=план(след.неделя, не используется), I=план(накопительно),
# J=факт(накопительно), K=%вып-я(накопительно), L=план(месячный),
# M=факт(месячный, В СТАРОЙ ФОРМЕ ВСЕГДА ПУСТО), N=%выполнения(месячный,
# всегда "0,00" пока M пусто — сама таблица не пересчитывает).
# "Факт (накопительно)" по факту И ЕСТЬ месячный факт нарастающим итогом
# (сбрасывается на новый месяц — проверено: 07-29 накопительно 121.6М
# (июль), 08-05 накопительно 9.58М (сброс, август) up к 08-12 43.1М) —
# используем его как "факт месячный" вместо пустой колонки M.
import re
import sys
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import json
import os

import gspread
from dotenv import load_dotenv

from db import get_connection, get_cursor
from sheets_retry import retry_google

load_dotenv()
SPREADSHEET_ID = "1IeTUyytfZkwvvzn4_V3M_hlyuCJFUqQySK49JzgZ9xI"

TAB_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
SECTION_HEADER_RE = re.compile(r"ГЛАВНЫЕ (?:МЕТРИКИ|СТАТИСТИКИ) КОМПАНИИ")

# 0-based column indices in the metric data row
COL_LABEL = 1
COL_PLAN_PREV_WEEK = 3
COL_FACT_WEEK = 5
COL_PLAN_CUMULATIVE = 8
COL_FACT_CUMULATIVE = 9
COL_PCT_CUMULATIVE = 10
COL_PLAN_MONTHLY = 11


def parse_number(raw: str) -> float | None:
    s = (raw or "").strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def latest_tab_name(sh) -> str:
    dated = []
    for ws in sh.worksheets():
        title = ws.title.strip()
        if TAB_DATE_RE.match(title):
            dated.append(title)
    if not dated:
        raise RuntimeError("No date-named tabs found in the spreadsheet")
    return max(dated)


def fetch_latest_sheet_rows() -> tuple[str, list[list[str]]]:
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    gc = gspread.service_account_from_dict(json.loads(sa_json))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы")
    tab_name = latest_tab_name(sh)
    rows = retry_google(
        lambda: sh.worksheet(tab_name).get_all_values(),
        what=f"чтение вкладки «{tab_name}»",
    )
    return tab_name.strip(), rows


def extract_metrics(rows: list[list[str]]) -> list[dict]:
    header_idx = None
    for i, row in enumerate(rows):
        if any(SECTION_HEADER_RE.search(cell) for cell in row):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError('Section "ГЛАВНЫЕ МЕТРИКИ КОМПАНИИ" not found in sheet')

    metrics = []
    # header_idx+1 is the column-header row ("Статистика", "План (предыдущей
    # недели)", ...) — data rows start at header_idx+2, continue while col B
    # (label) is non-empty.
    for row in rows[header_idx + 2:]:
        label = row[COL_LABEL].strip() if len(row) > COL_LABEL else ""
        if not label:
            break
        def cell(idx: int) -> str:
            return row[idx] if idx < len(row) else ""
        metrics.append({
            "metric_label": label,
            "plan_prev_week": parse_number(cell(COL_PLAN_PREV_WEEK)),
            "fact_week": parse_number(cell(COL_FACT_WEEK)),
            "plan_cumulative": parse_number(cell(COL_PLAN_CUMULATIVE)),
            "fact_cumulative": parse_number(cell(COL_FACT_CUMULATIVE)),
            "pct_cumulative": parse_number(cell(COL_PCT_CUMULATIVE)),
            "plan_monthly": parse_number(cell(COL_PLAN_MONTHLY)),
        })
    return metrics


def upsert_rows(meeting_date: str, rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO leaders_protocol_metrics
            (meeting_date, metric_label, plan_prev_week, fact_week,
             plan_cumulative, fact_cumulative, pct_cumulative, plan_monthly, loaded_at)
        VALUES %s
        ON CONFLICT (meeting_date, metric_label) DO UPDATE SET
            plan_prev_week   = EXCLUDED.plan_prev_week,
            fact_week        = EXCLUDED.fact_week,
            plan_cumulative  = EXCLUDED.plan_cumulative,
            fact_cumulative  = EXCLUDED.fact_cumulative,
            pct_cumulative   = EXCLUDED.pct_cumulative,
            plan_monthly     = EXCLUDED.plan_monthly,
            loaded_at        = EXCLUDED.loaded_at
    """, [
        (
            meeting_date, r["metric_label"], r["plan_prev_week"], r["fact_week"],
            r["plan_cumulative"], r["fact_cumulative"], r["pct_cumulative"], r["plan_monthly"],
            datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds"),
        )
        for r in rows
    ], page_size=100)
    conn.commit()
    conn.close()


def main():
    print("Fetching latest leaders-protocol sheet...", flush=True)
    meeting_date, rows = fetch_latest_sheet_rows()
    print(f"  Latest tab: {meeting_date}", flush=True)

    metrics = extract_metrics(rows)
    print(f"  {len(metrics)} metric row(s) parsed: {[m['metric_label'] for m in metrics]}", flush=True)

    upsert_rows(meeting_date, metrics)
    print(f"Upserted {len(metrics)} row(s) for {meeting_date} into leaders_protocol_metrics")


if __name__ == "__main__":
    main()
