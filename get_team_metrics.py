# Google Sheets -> PostgreSQL (Supabase): team_metrics_kitchen_monthly,
# team_metrics_courier_monthly
#
# Source: "Метрики команды" Google-таблица (HR-отдел), листы "Метрики
# команды" (кухня/линейный персонал) и "Метрики команды куры" (курьеры).
#
# Формат — вертикально повторяющийся блок: одна строка на пиццерию (7 штук)
# + одна строка "общ по сети", помечен в первой колонке текстом вида
# "Месяц [Год] Якутск-1" (год иногда пропущен — переносится с предыдущего
# найденного блока). Год всегда есть хотя бы у ПЕРВОГО блока в листе.
#
# ⚠️ Известная аномалия (сентябрь 2025, лист "Метрики команды"): между
# нормальными месячными блоками встречается получастичный подблок
# "Сентябрь 2025 с 01-15.09 Якутск-1" (первая половина месяца, без строки
# "общ по сети" в конце) — не совпадает с якорным regex (лишний текст между
# годом и "Якутск-1"), поэтому естественным образом пропускается.
#
# Не API-пул — ручной запуск (workflow_dispatch), таблица ведётся HR-отделом
# вручную, обновляется помесячно.
import json
import os
import re
from datetime import date

import gspread
from dotenv import load_dotenv

from db import get_connection, get_cursor
from sheets_retry import retry_google

load_dotenv()
SPREADSHEET_ID = "1VxE2k8bSrPJO-lnnkxiqaL418IYkYbhSHXxB-WOQecQ"

UNIT_NAMES = [f"Якутск-{i}" for i in range(1, 8)]
NETWORK_LABEL = "общ по сети"
NETWORK_UNIT = "Сеть"

MONTHS_RU = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
BLOCK_HEADER_RE = re.compile(
    r"^(?P<month>[А-Яа-яЁё]+)\s*(?P<year>\d{4})?\s*Якутск-1\s*$"
)


def parse_pct(raw: str) -> float | None:
    s = (raw or "").replace("\xa0", "").replace("\t", "").replace(" ", "").replace("%", "").replace(",", ".").strip()
    if not s or s in ("-", "х", "x"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(raw: str) -> int | None:
    s = (raw or "").replace("\xa0", "").replace(" ", "").strip()
    if not s or s in ("-", "х", "x"):
        return None
    try:
        return int(round(float(s.replace(",", "."))))
    except ValueError:
        return None


def find_month_blocks(vals: list[list[str]]) -> list[tuple[date, int]]:
    """Returns [(month_date, first_row_index), ...] for well-formed 8-row blocks
    (7 units + network total), skipping malformed/partial ones."""
    blocks = []
    last_year = None
    for i, row in enumerate(vals):
        label = (row[0] if row else "").strip()
        m = BLOCK_HEADER_RE.match(label)
        if not m:
            continue
        month_name = m.group("month").lower()
        if month_name not in MONTHS_RU:
            continue
        year = int(m.group("year")) if m.group("year") else last_year
        if year is None:
            continue  # no year ever seen yet — can't place this block in time
        last_year = year
        blocks.append((date(year, MONTHS_RU[month_name], 1), i))
    return blocks


def extract_rows(vals: list[list[str]], sheet_kind: str) -> list[dict]:
    blocks = find_month_blocks(vals)
    out = []
    for month, start in blocks:
        # Expect: start..start+6 = 7 units, start+7 = "общ по сети".
        # Some blocks are short (e.g. new/closed units) — match by row[1..7]
        # position within the block rather than assuming exactly 8 rows.
        block_rows = vals[start:start + 8]
        if len(block_rows) < 8:
            continue
        network_row = block_rows[7]
        if NETWORK_LABEL not in (network_row[0] if network_row else "").strip().lower():
            continue  # malformed block (e.g. missing a unit row) — skip rather than guess
        for idx, unit_name in enumerate(UNIT_NAMES):
            row = block_rows[idx]
            out.append(parse_row(sheet_kind, month, unit_name, row))
        out.append(parse_row(sheet_kind, month, NETWORK_UNIT, network_row))
    return out


def parse_row(sheet_kind: str, month: date, unit_name: str, row: list[str]) -> dict:
    def col(i):
        return row[i] if i < len(row) else ""

    base = {
        "month": month, "unit_name": unit_name,
        "completion_pct": parse_pct(col(2)),
        "turnover_pct": parse_pct(col(3) if sheet_kind == "kitchen" else col(4)),
        "hired_count": parse_int(col(6)),
        "terminated_count": parse_int(col(7)),
    }
    if sheet_kind == "kitchen":
        base["experienced_share_pct"] = parse_pct(col(5))
        exp, trainee, novice = parse_int(col(30)), parse_int(col(31)), parse_int(col(32))
        total = (exp or 0) + (trainee or 0) + (novice or 0)
        base["trainee_share_pct"] = round(trainee / total * 100, 2) if trainee is not None and total else None
    return base


def upsert_kitchen(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO team_metrics_kitchen_monthly
            (month, unit_name, completion_pct, turnover_pct, experienced_share_pct, trainee_share_pct, hired_count, terminated_count, loaded_at)
        VALUES %s
        ON CONFLICT (month, unit_name) DO UPDATE SET
            completion_pct        = EXCLUDED.completion_pct,
            turnover_pct          = EXCLUDED.turnover_pct,
            experienced_share_pct = EXCLUDED.experienced_share_pct,
            trainee_share_pct     = EXCLUDED.trainee_share_pct,
            hired_count           = EXCLUDED.hired_count,
            terminated_count      = EXCLUDED.terminated_count,
            loaded_at             = now()
    """, [
        (r["month"], r["unit_name"], r["completion_pct"], r["turnover_pct"],
         r["experienced_share_pct"], r["trainee_share_pct"], r["hired_count"], r["terminated_count"])
        for r in rows
    ], template="(%s,%s,%s,%s,%s,%s,%s,%s,now())", page_size=200)
    conn.commit()
    conn.close()
    print(f"team_metrics_kitchen_monthly: upserted {len(rows)} rows")


def upsert_courier(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO team_metrics_courier_monthly
            (month, unit_name, completion_pct, turnover_pct, hired_count, terminated_count, loaded_at)
        VALUES %s
        ON CONFLICT (month, unit_name) DO UPDATE SET
            completion_pct   = EXCLUDED.completion_pct,
            turnover_pct     = EXCLUDED.turnover_pct,
            hired_count      = EXCLUDED.hired_count,
            terminated_count = EXCLUDED.terminated_count,
            loaded_at        = now()
    """, [
        (r["month"], r["unit_name"], r["completion_pct"], r["turnover_pct"], r["hired_count"], r["terminated_count"])
        for r in rows
    ], template="(%s,%s,%s,%s,%s,%s,now())", page_size=200)
    conn.commit()
    conn.close()
    print(f"team_metrics_courier_monthly: upserted {len(rows)} rows")


def main():
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    gc = gspread.service_account_from_dict(json.loads(sa_json))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы")

    kitchen_vals = retry_google(
        lambda: sh.worksheet("Метрики команды").get_all_values(),
        what="чтение листа «Метрики команды»",
    )
    courier_vals = retry_google(
        lambda: sh.worksheet("Метрики команды куры").get_all_values(),
        what="чтение листа «Метрики команды куры»",
    )

    upsert_kitchen(extract_rows(kitchen_vals, "kitchen"))
    upsert_courier(extract_rows(courier_vals, "courier"))


if __name__ == "__main__":
    main()
