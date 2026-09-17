# Google Sheets -> PostgreSQL (Supabase): leaders_protocol_metrics_monthly
#
# Source: "Протокол совета руководителей Додо Якутск по итогу месяца" —
# ЕЖЕМЕСЯЧНЫЙ протокол совета руководителей (отдельная таблица от
# еженедельного "Протокол встречи лидеров", см. get_leaders_protocol.py).
# Один лист на встречу. Исторически вкладки называются полной датой встречи
# ("YYYY-MM-DD"), но последние — просто "YYYY-MM" (см. обсуждение с
# пользователем 2026-08-24).
#
# ⚠️ Вкладка для СЛЕДУЮЩЕЙ встречи создаётся заранее дублированием текущей и
# первое время содержит те же цифры и ту же "Дату проведения", что и
# предыдущая, пока её не заполнят по факту встречи (подтверждено на
# 2026-07/2026-08: обе вкладки были побайтово идентичны по цифрам). Поэтому
# берём самую свежую вкладку по имени, но если её метрики совпадают 1-в-1 со
# следующей по свежести — считаем её ещё не заполненным шаблоном и уходим на
# вкладку старше, пока не найдём отличающуюся (или не кончатся вкладки).
#
# Блок "1. ГЛАВНЫЕ СТАТИСТИКИ КОМПАНИИ ( ДЕНЬГИ)" — фиксированная раскладка
# (проверено на вкладках 2026-07, 2026-08):
#   строка с заголовком -> следующая строка (заголовки колонок) -> далее
#   одна строка на метрику (сейчас только "Выручка"), до первой строки с
#   пустой колонкой B (label).
# Колонки (0-based): B=label, D=план(отчётный месяц), F=факт(отчётный месяц),
# G=план(след. месяц), I=план(накопительно с начала года),
# J=факт(накопительно), K=%вып-я(накопительно), L=план(месячный,
# дублирует D), M=факт(месячный, дублирует F), N=%выполнения(месячный).
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
SPREADSHEET_ID = "1op3Fe_Bz6hpnq24-0FDgp6J_8LFdY0BvG2LGhluPDE8"

TAB_DATE_RE = re.compile(r"^(\d{4})-(\d{2})(-(\d{2}))?$")
SECTION_HEADER_RE = re.compile(r"ГЛАВНЫЕ (?:МЕТРИКИ|СТАТИСТИКИ) КОМПАНИИ")

# 0-based column indices in the metric data row
COL_LABEL = 1
COL_PLAN_MONTH = 3
COL_FACT_MONTH = 5
COL_PLAN_NEXT_MONTH = 6
COL_PLAN_CUMULATIVE = 8
COL_FACT_CUMULATIVE = 9
COL_PCT_CUMULATIVE = 10
COL_PLAN_MONTHLY = 11
COL_FACT_MONTHLY = 12
COL_PCT_MONTHLY = 13


def parse_number(raw: str) -> float | None:
    s = (raw or "").strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def dated_tab_names(sh) -> list[str]:
    """Все вкладки, чьё (обрезанное от пробелов) имя похоже на дату/месяц,
    отсортированные по возрастанию (строковая сортировка ISO-формата)."""
    dated = []
    for ws in sh.worksheets():
        title = ws.title.strip()
        if TAB_DATE_RE.match(title):
            dated.append(title)
    if not dated:
        raise RuntimeError("No date-named tabs found in the spreadsheet")
    dated.sort()
    return dated


def extract_metrics(rows: list[list[str]]) -> list[dict]:
    header_idx = None
    for i, row in enumerate(rows):
        if any(SECTION_HEADER_RE.search(cell) for cell in row):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError('Section "ГЛАВНЫЕ .. КОМПАНИИ" not found in sheet')

    metrics = []
    for row in rows[header_idx + 2:]:
        label = row[COL_LABEL].strip() if len(row) > COL_LABEL else ""
        if not label:
            break

        def cell(idx: int) -> str:
            return row[idx] if idx < len(row) else ""

        metrics.append({
            "metric_label": label,
            "plan_month": parse_number(cell(COL_PLAN_MONTH)),
            "fact_month": parse_number(cell(COL_FACT_MONTH)),
            "plan_next_month": parse_number(cell(COL_PLAN_NEXT_MONTH)),
            "plan_cumulative": parse_number(cell(COL_PLAN_CUMULATIVE)),
            "fact_cumulative": parse_number(cell(COL_FACT_CUMULATIVE)),
            "pct_cumulative": parse_number(cell(COL_PCT_CUMULATIVE)),
            "plan_monthly": parse_number(cell(COL_PLAN_MONTHLY)),
            "fact_monthly": parse_number(cell(COL_FACT_MONTHLY)),
            "pct_monthly": parse_number(cell(COL_PCT_MONTHLY)),
        })
    return metrics


def metrics_numerically_equal(a: list[dict], b: list[dict]) -> bool:
    """Сравнение без учёта metric_label — вкладка-шаблон дублирует все
    числовые колонки предыдущей встречи, но переименовывает label (месяц)."""
    if len(a) != len(b):
        return False
    numeric_keys = [k for k in a[0].keys() if k != "metric_label"] if a else []
    a_sorted = sorted(a, key=lambda m: m["metric_label"])
    b_sorted = sorted(b, key=lambda m: m["metric_label"])
    for ra, rb in zip(a_sorted, b_sorted):
        if any(ra[k] != rb[k] for k in numeric_keys):
            return False
    return True


def fetch_latest_filled_sheet() -> tuple[str, list[dict]]:
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    gc = gspread.service_account_from_dict(json.loads(sa_json))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы")
    tabs = dated_tab_names(sh)  # ascending

    cache: dict[str, list[dict]] = {}

    def metrics_for(tab: str) -> list[dict]:
        if tab not in cache:
            cache[tab] = extract_metrics(
                retry_google(
                    lambda: sh.worksheet(tab).get_all_values(),
                    what=f"чтение вкладки «{tab}»",
                )
            )
        return cache[tab]

    # Идём от самой свежей вкладки назад, пока не найдём такую, что либо
    # это самая старая вкладка вообще, либо она отличается от следующей
    # более старой — то есть реально была заполнена по итогам встречи.
    for i in range(len(tabs) - 1, -1, -1):
        tab = tabs[i]
        current = metrics_for(tab)
        if i == 0:
            return tab, current
        older = metrics_for(tabs[i - 1])
        if not metrics_numerically_equal(current, older):
            return tab, current
        print(f"  Skipping '{tab}' — identical to '{tabs[i - 1]}', looks like an unfilled template", flush=True)

    # Все вкладки идентичны — возвращаем самую свежую как есть.
    return tabs[-1], cache[tabs[-1]]


def upsert_rows(report_period: str, rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO leaders_protocol_metrics_monthly
            (report_period, metric_label, plan_month, fact_month, plan_next_month,
             plan_cumulative, fact_cumulative, pct_cumulative,
             plan_monthly, fact_monthly, pct_monthly, loaded_at)
        VALUES %s
        ON CONFLICT (report_period, metric_label) DO UPDATE SET
            plan_month       = EXCLUDED.plan_month,
            fact_month       = EXCLUDED.fact_month,
            plan_next_month  = EXCLUDED.plan_next_month,
            plan_cumulative  = EXCLUDED.plan_cumulative,
            fact_cumulative  = EXCLUDED.fact_cumulative,
            pct_cumulative   = EXCLUDED.pct_cumulative,
            plan_monthly     = EXCLUDED.plan_monthly,
            fact_monthly     = EXCLUDED.fact_monthly,
            pct_monthly      = EXCLUDED.pct_monthly,
            loaded_at        = EXCLUDED.loaded_at
    """, [
        (
            report_period, r["metric_label"], r["plan_month"], r["fact_month"], r["plan_next_month"],
            r["plan_cumulative"], r["fact_cumulative"], r["pct_cumulative"],
            r["plan_monthly"], r["fact_monthly"], r["pct_monthly"],
            datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds"),
        )
        for r in rows
    ], page_size=100)
    conn.commit()
    conn.close()


def main():
    print("Fetching latest filled leaders-protocol-monthly sheet...", flush=True)
    report_period, metrics = fetch_latest_filled_sheet()
    print(f"  Latest filled tab: {report_period}", flush=True)
    print(f"  {len(metrics)} metric row(s) parsed: {[m['metric_label'] for m in metrics]}", flush=True)

    upsert_rows(report_period, metrics)
    print(f"Upserted {len(metrics)} row(s) for {report_period} into leaders_protocol_metrics_monthly")


if __name__ == "__main__":
    main()
