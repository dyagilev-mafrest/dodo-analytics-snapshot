import json
from datetime import date, timedelta

from get_product_sales import (
    make_headers, fetch_sales, aggregate_product_revenue, upsert_rows,
    aggregate_discount_categories, upsert_discount_category_rows,
)

UNITS_FILE = "yakutsk_units.json"
DAYS = 56


def main():
    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=DAYS - 1)
    days = [str(start_date + timedelta(days=i)) for i in range(DAYS)]

    print(f"Backfill: {start_date} - {end_date} ({DAYS} days) x {len(units)} units", flush=True)

    for i, day in enumerate(days, 1):
        print(f"\n[{i}/{len(days)}] {day}", flush=True)
        headers = make_headers()
        all_rows = []
        all_category_rows = []
        for unit in units:
            orders = fetch_sales(unit["id"], day, headers)
            rows = aggregate_product_revenue(orders, day, unit["id"], unit.get("name"))
            all_rows.extend(rows)
            all_category_rows.extend(aggregate_discount_categories(orders, day, unit["id"]))
        upsert_rows(all_rows)
        upsert_discount_category_rows(all_category_rows)
        print(f"  upserted {len(all_rows)} rows ({len(all_category_rows)} discount-category rows)", flush=True)

    print("\nDone.")


if __name__ == "__main__":
    main()
