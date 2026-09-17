# Google Sheets -> PostgreSQL (Supabase): warehouse_writeoffs_monthly
#
# Лист «Списание ТМЦ» таблицы «Движение ТМЦ» — журнал актов списания, строка на
# акт. Складываем до зерна «месяц × склад × причина»; сумма по месяцу идёт
# строкой «Списания ТМЦ» месячного отчёта.
#
# Читается живьём, как и остатки (см. load_warehouse_stock_monthly.py).
#
# ⚠️ Месяц без строк — это НОЛЬ, а не пробел: списания случаются не каждый
# месяц (в 2026 их не было в апреле и августе). Отличить ноль от «ещё не
# внесли» можно только по границе журнала, поэтому читатель сравнивает месяц с
# максимальным в таблице. Здесь же журнал перезаписывается целиком — акт могут
# задним числом поправить или удалить, и upsert оставил бы призрак.
#
# Usage:
#   python load_warehouse_writeoffs_monthly.py            # прочитать и загрузить
#   python load_warehouse_writeoffs_monthly.py --dry-run  # показать и не писать
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
SHEET_NAME = "Списание ТМЦ"

# Колонки: дата, номенклатура, серия, склад, комментарий, количество, цена, сумма.
COL_DATE, COL_WAREHOUSE, COL_REASON, COL_SUM = 0, 3, 4, 7
HEADER = {
    COL_DATE: "Дата",
    COL_WAREHOUSE: "Ордер на отражение недостач товаров.Склад",
    COL_REASON: "Коментарий",
    COL_SUM: "Сумма",
}


def parse_rub(raw: str) -> float | None:
    s = raw or ""
    for ch in (" ", " ", " ", " "):  # неразрывные и узкие пробелы
        s = s.replace(ch, "")
    s = s.replace("−", "-").replace(",", ".").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_month(raw: str) -> date | None:
    """«23.07.2026» -> date(2026, 7, 1). День бывает без ведущего нуля («1.11.2025»)."""
    parts = (raw or "").strip().split(".")
    if len(parts) != 3:
        return None
    try:
        _, month, year = int(parts[0]), int(parts[1]), int(parts[2])
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

    # Разметку ведут руками, а «Цена» стоит прямо перед «Суммой» — сложить не ту
    # колонку загрузчик не должен даже в теории.
    header = [(c or "").strip() for c in vals[0]]
    for col, expected in HEADER.items():
        got = header[col] if col < len(header) else ""
        if got != expected:
            raise ValueError(
                f"Колонка {col + 1} листа «{SHEET_NAME}» называется {got!r}, а ожидается "
                f"{expected!r} — разметку поменяли, загрузчик надо править."
            )

    totals: dict[tuple, list] = defaultdict(lambda: [0.0, 0])
    skipped = 0
    for row in vals[1:]:
        if len(row) <= COL_SUM:
            continue
        month = parse_month(row[COL_DATE])
        amount = parse_rub(row[COL_SUM])
        if month is None or amount is None:
            skipped += 1
            continue
        key = (month, (row[COL_WAREHOUSE] or "").strip(), (row[COL_REASON] or "").strip())
        totals[key][0] += amount
        totals[key][1] += 1

    if not totals:
        raise ValueError(f"На листе «{SHEET_NAME}» не разобрано ни одного акта.")
    print(f"Разобрано {len(totals)} сочетаний «месяц/склад/причина», пропущено строк: {skipped}")
    return [
        {"month": m, "warehouse": w, "reason": r, "writeoff_rub": v, "acts_count": n}
        for (m, w, r), (v, n) in sorted(totals.items())
    ]


def replace_all(rows: list[dict]):
    """Журнал перезаписывается целиком: акт могут поправить задним числом или
    удалить, и upsert оставил бы в таблице строку, которой в источнике нет."""
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("DELETE FROM warehouse_writeoffs_monthly")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO warehouse_writeoffs_monthly (month, warehouse, reason, writeoff_rub, acts_count)
        VALUES %s
    """, [(r["month"], r["warehouse"], r["reason"], r["writeoff_rub"], r["acts_count"]) for r in rows],
        page_size=500)
    conn.commit()
    conn.close()


def main():
    rows = read_rows()
    by_month: dict[date, float] = defaultdict(float)
    for r in rows:
        by_month[r["month"]] += r["writeoff_rub"]
    months = sorted(by_month)
    print(f"\nЖурнал: {months[0]} - {months[-1]}, месяцев со списаниями: {len(months)}")
    print("месяц       списано")
    for month in months[-14:]:
        print(f"  {month}  {by_month[month]:>14,.0f}")
    print("  (месяцы, которых тут нет, внутри этого диапазона — ноль списаний)")

    if "--dry-run" in sys.argv:
        print("\n--dry-run: в БД не пишем.")
        return
    replace_all(rows)
    print(f"\nЗагружено {len(rows)} строк в warehouse_writeoffs_monthly.")


if __name__ == "__main__":
    main()
