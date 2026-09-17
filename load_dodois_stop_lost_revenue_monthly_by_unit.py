# Loads the manually-exported Dodo IS Superset pivot-table xlsx (per unit, per
# month: revenue + share of lost sales from product/key-ingredient stops) into
# dodois_product_stops_lost_revenue_monthly_by_unit — powers the "По пиццериям"
# view of the "Стопы продуктов и ингредиентов" trend charts (Месяц). See
# migrations/041_dodois_product_stops_lost_revenue_monthly_by_unit.sql.
#
# Not an API pull — no scheduled job; re-run manually whenever a fresh export
# is provided (each export is a full-year cumulative pivot, so a re-run
# naturally overwrites/extends prior months via ON CONFLICT).
#
# Usage:
#   python load_dodois_stop_lost_revenue_monthly_by_unit.py <pivot_export.xlsx>
#
# Expected columns: Период | Пиццерия | Партнер | Выручка |
#                    Доля упущенной выручки от стопов продуктов |
#                    Доля упущенной выручки от стопов ключевых ингредиентов
# (share columns arrive as fractions of 1 — converted to percent-of-100 and
# used to derive an absolute ₽ figure: share = lost/(lost+revenue) =>
# lost = share*revenue/(1-share).)
import sys

import openpyxl

from db import get_connection, get_cursor


def derive_lost_rub(share_fraction, revenue):
    if share_fraction is None or revenue is None:
        return None
    if share_fraction >= 1:
        return None
    return share_fraction * revenue / (1 - share_fraction)


def load_rows(path: str, sheet: str = "Sheet1") -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    rows = []
    for period, unit_name, _partner, revenue, products_share, key_ing_share in ws.iter_rows(min_row=2, values_only=True):
        if period is None or unit_name is None:
            continue
        month = period.date() if hasattr(period, "date") else period
        rows.append({
            "month": month,
            "unit_name": unit_name,
            "revenue": revenue,
            "products_pct": products_share * 100 if products_share is not None else None,
            "key_ingredients_pct": key_ing_share * 100 if key_ing_share is not None else None,
            "products_rub": derive_lost_rub(products_share, revenue),
            "key_ingredients_rub": derive_lost_rub(key_ing_share, revenue),
        })
    return rows


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO dodois_product_stops_lost_revenue_monthly_by_unit
            (month, unit_name, revenue_rub, lost_revenue_products_pct, lost_revenue_key_ingredients_pct,
             lost_revenue_products_rub, lost_revenue_key_ingredients_rub, loaded_at)
        VALUES %s
        ON CONFLICT (month, unit_name) DO UPDATE SET
            revenue_rub                       = EXCLUDED.revenue_rub,
            lost_revenue_products_pct         = EXCLUDED.lost_revenue_products_pct,
            lost_revenue_key_ingredients_pct  = EXCLUDED.lost_revenue_key_ingredients_pct,
            lost_revenue_products_rub         = EXCLUDED.lost_revenue_products_rub,
            lost_revenue_key_ingredients_rub  = EXCLUDED.lost_revenue_key_ingredients_rub,
            loaded_at                         = now()
    """, [
        (row["month"], row["unit_name"], row["revenue"], row["products_pct"], row["key_ingredients_pct"],
         row["products_rub"], row["key_ingredients_rub"])
        for row in rows
    ], template="(%s,%s,%s,%s,%s,%s,%s,now())", page_size=200)
    conn.commit()
    conn.close()
    print(f"Loaded {len(rows)} (month, unit) rows")


if __name__ == "__main__":
    rows = load_rows(sys.argv[1])
    upsert_rows(rows)
