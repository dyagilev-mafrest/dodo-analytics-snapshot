# Бэкфилл drink_sku_daily_sales — сеть-агрегат продаж напитков по SKU за день,
# источник для прогноза закупок напитков (сен.2026-янв.2027).
#
# Тянет accounting/sales так же, как get_product_sales.py, но суммирует qty/revenue
# сразу по всем точкам и каналам продаж (не пишет per-unit/per-channel строки).
#
# Локальный расчёт (для внутреннего прогноза закупок, не для дашборда/прода) —
# пишет в локальный SQLite (dodo.db), НЕ в Supabase, вне зависимости от
# USE_CLOUD_DB в .env, чтобы не раздувать квоту Supabase Free (см. project memory
# про 500MB лимит и обрезку product_daily_revenue).
import json
import sqlite3
import time
from collections import defaultdict
from datetime import date, timedelta

from get_product_sales import make_headers, fetch_sales

UNITS_FILE = "yakutsk_units.json"
DRINK_CATEGORY = "Напитки"
DB_PATH = "dodo.db"


def get_local_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS drink_sku_daily_sales (
            date         TEXT NOT NULL,
            product_id   TEXT NOT NULL,
            product_name TEXT,
            qty          INTEGER NOT NULL DEFAULT 0,
            revenue      REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (date, product_id)
        )
    """)
    conn.commit()
    return conn


def aggregate_drinks(orders: list[dict]) -> dict[str, dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "name": None})
    for order in orders:
        for p in order.get("products", []):
            if p.get("productCategoryName") != DRINK_CATEGORY:
                continue
            row = agg[p["productId"]]
            row["qty"] += 1
            row["revenue"] += p.get("priceWithDiscount") or 0
            row["name"] = p.get("defaultProductName")
    return agg


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    conn = get_local_connection()
    conn.executemany("""
        INSERT OR REPLACE INTO drink_sku_daily_sales (date, product_id, product_name, qty, revenue)
        VALUES (?, ?, ?, ?, ?)
    """, [
        (row["date"], row["product_id"], row["product_name"], row["qty"], row["revenue"])
        for row in rows
    ])
    conn.commit()
    conn.close()


def date_range(start: str, end: str):
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        yield str(d)
        d += timedelta(days=1)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    days = list(date_range(args.from_date, args.to_date))
    total = len(days) * len(units)
    done = 0

    def fetch_unit(day: str, unit: dict, headers: dict):
        orders = fetch_sales(unit["id"], day, headers)
        return unit, orders

    headers = make_headers()
    for day in days:
        print(f"\n[{day}]", flush=True)
        day_agg: dict[str, dict] = defaultdict(lambda: {"qty": 0, "revenue": 0.0, "name": None})
        # Sequential per unit (not threaded across units) — a prior threaded run
        # tripped Dodo IS 429s and, worse, burned the one-time-use refresh_token
        # via concurrent force-refreshes. One shared `headers` dict avoids that.
        for unit in units:
            unit, orders = fetch_unit(day, unit, headers)
            unit_agg = aggregate_drinks(orders)
            for product_id, v in unit_agg.items():
                row = day_agg[product_id]
                row["qty"] += v["qty"]
                row["revenue"] += v["revenue"]
                row["name"] = v["name"]
            done += 1
            print(f"  [{done}/{total}] {unit.get('name')}: {len(orders)} заказов", flush=True)
            time.sleep(0.3)

        rows = [
            {"date": day, "product_id": pid, "product_name": v["name"], "qty": v["qty"], "revenue": v["revenue"]}
            for pid, v in day_agg.items()
        ]
        upsert_rows(rows)
        print(f"  upserted {len(rows)} SKU-строк напитков", flush=True)

    print("Done.")


if __name__ == "__main__":
    main()
