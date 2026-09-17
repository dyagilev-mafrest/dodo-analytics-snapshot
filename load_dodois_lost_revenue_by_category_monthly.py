# Loads the manually-exported Dodo IS Superset xlsx (lost sales by product
# category, one column per category) into dodois_lost_revenue_by_category_monthly
# — powers "Динамика упущенной выручки по категориям" in "Стопы продуктов и
# ингредиентов" (Месяц). See migrations/042_dodois_lost_revenue_by_category_monthly.sql.
#
# Not an API pull — no scheduled job; re-run manually whenever a fresh export
# is provided. Wide -> long: first column is the period, every other column
# header is a category name.
#
# Usage:
#   python load_dodois_lost_revenue_by_category_monthly.py <categories_export.xlsx>
import sys

import openpyxl

from db import get_connection, get_cursor


def load_rows(path: str, sheet: str = "Sheet1") -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter)
    categories = header[1:]
    rows = []
    for row in rows_iter:
        period = row[0]
        if period is None:
            continue
        month = period.date() if hasattr(period, "date") else period
        for category, value in zip(categories, row[1:]):
            if category is None:
                continue
            rows.append({"month": month, "category_name": category, "lost_revenue_rub": value})
    return rows


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO dodois_lost_revenue_by_category_monthly (month, category_name, lost_revenue_rub, loaded_at)
        VALUES %s
        ON CONFLICT (month, category_name) DO UPDATE SET
            lost_revenue_rub = EXCLUDED.lost_revenue_rub,
            loaded_at        = now()
    """, [(row["month"], row["category_name"], row["lost_revenue_rub"]) for row in rows],
    template="(%s,%s,%s,now())", page_size=200)
    conn.commit()
    conn.close()
    print(f"Loaded {len(rows)} (month, category) rows")


if __name__ == "__main__":
    rows = load_rows(sys.argv[1])
    upsert_rows(rows)
