# Loads the manually-exported Dodo IS Superset xlsx ("Discount Analytics.
# Dynamic of discount") into dodois_discount_monthly. See
# migrations/045_dodois_discount_monthly.sql for why this is a manual export
# rather than a live API pull (same temporary-override pattern as the other
# dodois_*_monthly tables).
#
# Not an API pull — no scheduled job; re-run manually whenever a fresh export
# is provided. Only current-year data is kept — table stays tiny.
#
# Usage:
#   python load_dodois_discount_monthly.py <discount_dynamics.xlsx>
#
# Columns: SaleDate | Доставка | Ресторан | Доля дисконта от выручки
# (values arrive as fractions of 1 — stored as-is, NOT converted to
# percent-of-100 like the other dodois_*_monthly tables; frontend multiplies
# by 100 for display, see useDodoIsDiscountMonthly.ts)
import sys

import openpyxl

from db import get_connection, get_cursor


def load_rows(path: str, sheet: str = "Sheet1") -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    rows = []
    for period, delivery, restaurant, overall in ws.iter_rows(min_row=2, values_only=True):
        if period is None:
            continue
        month = period.date() if hasattr(period, "date") else period
        rows.append({
            "month": month,
            "delivery_discount_pct": delivery,
            "restaurant_discount_pct": restaurant,
            "overall_discount_pct": overall,
        })
    return rows


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO dodois_discount_monthly
            (month, delivery_discount_pct, restaurant_discount_pct, overall_discount_pct, loaded_at)
        VALUES %s
        ON CONFLICT (month) DO UPDATE SET
            delivery_discount_pct   = EXCLUDED.delivery_discount_pct,
            restaurant_discount_pct = EXCLUDED.restaurant_discount_pct,
            overall_discount_pct    = EXCLUDED.overall_discount_pct,
            loaded_at               = now()
    """, [
        (row["month"], row["delivery_discount_pct"], row["restaurant_discount_pct"], row["overall_discount_pct"])
        for row in rows
    ], template="(%s,%s,%s,%s,now())", page_size=200)
    conn.commit()
    conn.close()
    print(f"Loaded {len(rows)} months")


if __name__ == "__main__":
    rows = load_rows(sys.argv[1])
    upsert_rows(rows)
