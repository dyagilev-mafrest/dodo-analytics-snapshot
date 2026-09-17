# Google Sheets -> PostgreSQL (Supabase): warehouse_stock_monthly
#
# Лист «Остатки ТМЦ месяц» таблицы «Движение ТМЦ» — остатки ТМЦ по сети на конец
# месяца, строка на позицию номенклатуры. Складываем до зерна «месяц × склад ×
# группа»: сумма по месяцу и есть «Остатки товарного запаса» месячного отчёта.
#
# Читается живьём, а не выгрузкой: у сервисного аккаунта доступ к таблице есть,
# и лишний ручной шаг тут не нужен. См. migrations/069 о том, почему это не
# свёртка недельных снимков warehouse_stock.
#
# Usage:
#   python load_warehouse_stock_monthly.py            # прочитать и загрузить
#   python load_warehouse_stock_monthly.py --dry-run  # показать и не писать
import json
import os
import sys
from collections import defaultdict
from datetime import date

import gspread
from dotenv import load_dotenv

from db import get_connection, get_cursor
from sheets_retry import retry_google

load_dotenv()
SPREADSHEET_ID = "1GXCWU8z1cSqCpPzawQCXAUUFLxvkoZegZQVTh1mvpjQ"
SHEET_NAME = "Остатки ТМЦ месяц"

# Колонки листа по порядку: дата, склад, номенклатура, группа, ед.изм.,
# количество, оценка, цена. Берём дату, склад, группу и оценку.
COL_DATE, COL_WAREHOUSE, COL_GROUP, COL_VALUE = 0, 1, 3, 6
HEADER = ("Дата", "Склад", "Группа", "Оценка")


def parse_rub(raw: str) -> float | None:
    s = raw or ""
    for ch in ("\xa0", " ", " ", " "):
        s = s.replace(ch, "")
    s = s.replace("−", "-").replace(",", ".").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_month(raw: str) -> date | None:
    """«08.2026» -> date(2026, 8, 1)."""
    parts = (raw or "").strip().split(".")
    if len(parts) != 2:
        return None
    try:
        month, year = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not 1 <= month <= 12:
        return None
    return date(year, month, 1)


def read_rows() -> list[dict]:
    gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы «Движение ТМЦ»")
    vals = retry_google(
        lambda: sh.worksheet(SHEET_NAME).get_all_values(),
        what=f"чтение листа «{SHEET_NAME}»",
    )
    if not vals:
        raise ValueError(f"Лист «{SHEET_NAME}» пуст.")

    # Разметку листа ведут руками: если колонки переставят, молча сложить
    # «количество» вместо «оценки» — худшее, что может сделать загрузчик.
    header = [(c or "").strip() for c in vals[0]]
    for col, expected in zip((COL_DATE, COL_WAREHOUSE, COL_GROUP, COL_VALUE), HEADER):
        got = header[col] if col < len(header) else ""
        if got != expected:
            raise ValueError(
                f"Колонка {col + 1} листа «{SHEET_NAME}» называется {got!r}, а ожидается "
                f"{expected!r} — разметку поменяли, загрузчик надо править."
            )

    totals: dict[tuple, float] = defaultdict(float)
    skipped = 0
    for row in vals[1:]:
        if len(row) <= COL_VALUE:
            continue
        month = parse_month(row[COL_DATE])
        value = parse_rub(row[COL_VALUE])
        # Пустая «Оценка» — позиция с нулевым остатком; ноль рублей и отсутствие
        # позиции на складе это одно и то же, строку не заводим.
        if month is None or value is None:
            skipped += 1
            continue
        key = (month, (row[COL_WAREHOUSE] or "").strip(), (row[COL_GROUP] or "").strip())
        totals[key] += value

    if not totals:
        raise ValueError(f"На листе «{SHEET_NAME}» не разобрано ни одной строки.")
    print(f"Разобрано {len(totals)} сочетаний «месяц/склад/группа», пропущено строк: {skipped}")
    return [
        {"month": m, "warehouse": w, "group_name": g, "stock_rub": v}
        for (m, w, g), v in sorted(totals.items())
    ]


def upsert(rows: list[dict]):
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO warehouse_stock_monthly (month, warehouse, group_name, stock_rub)
        VALUES %s
        ON CONFLICT (month, warehouse, group_name) DO UPDATE
           SET stock_rub = EXCLUDED.stock_rub, loaded_at = now()
    """, [(r["month"], r["warehouse"], r["group_name"], r["stock_rub"]) for r in rows],
        page_size=500)
    conn.commit()
    conn.close()


def main():
    rows = read_rows()
    by_month: dict[date, float] = defaultdict(float)
    for r in rows:
        by_month[r["month"]] += r["stock_rub"]
    print("\nмесяц       остаток ТМЦ по сети")
    for month in sorted(by_month):
        print(f"  {month}  {by_month[month]:>18,.0f}")

    if "--dry-run" in sys.argv:
        print("\n--dry-run: в БД не пишем.")
        return
    upsert(rows)
    print(f"\nЗагружено {len(rows)} строк в warehouse_stock_monthly.")


if __name__ == "__main__":
    main()
