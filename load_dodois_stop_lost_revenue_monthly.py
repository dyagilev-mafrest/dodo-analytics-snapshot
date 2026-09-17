# Loads the manually-exported Dodo IS Superset (dashboard 111) xlsx pair into
# dodois_product_stops_lost_revenue_monthly — a TEMPORARY override for the
# "Стопы продуктов"/"Ключевые ингредиенты" metrics in dodo-analytics-dashboard's
# "Месяц" section, while our own unit+reason+time-overlap heuristic
# (usePulseWeekStopsProductsData.ts) over/undercounts on long periods. See
# migrations/040_dodois_product_stops_lost_revenue_monthly.sql for why.
#
# Not an API pull — no scheduled job; re-run manually whenever a fresh pair of
# exports is provided (each export is a full-year cumulative series, so a
# re-run naturally overwrites/extends prior months via ON CONFLICT).
#
# Usage:
#   python load_dodois_stop_lost_revenue_monthly.py <rub_export> <pct_export>
#
# Принимает и .xlsx, и .csv: Superset отдаёт то одно, то другое в зависимости от
# того, как выгружает человек. Разметка колонок у обоих одна и та же.
#
# rub_export columns:  Период | Упущенная выручка по стопам продуктов | Упущенная выручка от стопов ключевых ингредиентов
# pct_export columns:  Период | Доля упущенной выручки от стопов продуктов | Доля упущенной выручки от стопов ключевых ингредиентов
# (pct values arrive as fractions of 1, e.g. 0.0148630641644516 — converted to
# percent-of-100 on load to match the rest of the codebase's `_pct` convention.)
import csv
import io
import sys
from datetime import datetime

import openpyxl

from db import get_connection, get_cursor


def read_table(path: str, sheet: str = "Sheet1") -> list[tuple]:
    """Строки выгрузки без заголовка: (период, продукты, ключевые ингредиенты).

    CSV из Superset — точка с запятой, BOM, точка как десятичный знак и дата
    строкой; xlsx отдаёт datetime и числа. Приводим к одному виду здесь, чтобы
    ниже по коду разницы между форматами не было вовсе.
    """
    if path.lower().endswith(".csv"):
        with io.open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh, delimiter=";"))
        out = []
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            month = datetime.strptime(row[0].strip()[:10], "%Y-%m-%d").date().replace(day=1)
            vals = [float(c) if (c or "").strip() else None for c in row[1:3]]
            while len(vals) < 2:
                vals.append(None)
            out.append((month, vals[0], vals[1]))
        return out

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    out = []
    for period, products, key_ingredients in ws.iter_rows(min_row=2, values_only=True):
        if period is None:
            continue
        month = period.date() if hasattr(period, "date") else period
        out.append((month.replace(day=1), products, key_ingredients))
    return out


def load_rub(path: str) -> dict:
    return {
        month: {"products_rub": products, "key_ingredients_rub": key_ingredients}
        for month, products, key_ingredients in read_table(path)
    }


def load_pct(path: str) -> dict:
    return {
        month: {
            "products_pct": products * 100 if products is not None else None,
            "key_ingredients_pct": key_ingredients * 100 if key_ingredients is not None else None,
        }
        for month, products, key_ingredients in read_table(path)
    }


def upsert_rows(rub: dict, pct: dict):
    months = sorted(set(rub) | set(pct))
    rows = []
    for month in months:
        r = rub.get(month, {})
        p = pct.get(month, {})
        rows.append((
            month,
            r.get("products_rub"), r.get("key_ingredients_rub"),
            p.get("products_pct"), p.get("key_ingredients_pct"),
        ))
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO dodois_product_stops_lost_revenue_monthly
            (month, lost_revenue_products_rub, lost_revenue_key_ingredients_rub,
             lost_revenue_products_pct, lost_revenue_key_ingredients_pct, loaded_at)
        VALUES %s
        ON CONFLICT (month) DO UPDATE SET
            lost_revenue_products_rub        = EXCLUDED.lost_revenue_products_rub,
            lost_revenue_key_ingredients_rub = EXCLUDED.lost_revenue_key_ingredients_rub,
            lost_revenue_products_pct        = EXCLUDED.lost_revenue_products_pct,
            lost_revenue_key_ingredients_pct = EXCLUDED.lost_revenue_key_ingredients_pct,
            loaded_at                        = now()
    """, rows, template="(%s,%s,%s,%s,%s,now())", page_size=200)
    conn.commit()
    conn.close()
    print(f"Loaded {len(rows)} months: {months[0]} .. {months[-1]}")


if __name__ == "__main__":
    rub_path, pct_path = sys.argv[1], sys.argv[2]
    rub = load_rub(rub_path)
    pct = load_pct(pct_path)
    upsert_rows(rub, pct)
