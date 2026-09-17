# Backfill product_daily_revenue for the trailing N days (default 90).
# 90 days is enough to seed compute_lost_revenue.py's "3 preceding same-weekday"
# baseline for any stop in that window — accounting/sales is order-level and
# expensive to paginate, so we don't backfill full history like other tables.
import json
from datetime import date, timedelta

from get_product_sales import (
    make_headers, fetch_sales, aggregate_product_revenue, upsert_rows,
    aggregate_discount_categories, upsert_discount_category_rows,
)

UNITS_FILE = "yakutsk_units.json"


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="how many trailing days to backfill")
    args = parser.parse_args()

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=args.days - 1)
    days = [str(start_date + timedelta(days=i)) for i in range(args.days)]

    print(f"План: {start_date} — {end_date} ({args.days} дней) × {len(units)} точек")
    print(f"Итого запросов к API: до {args.days * len(units)} (плюс пагинация по 100 заказов)")
    answer = input("Начать бэкфилл? (да/нет): ").strip().lower()
    if answer not in ("да", "д", "yes", "y"):
        print("Отменено.")
        return

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
            print(f"  {unit.get('name')}: {len(orders)} заказов -> {len(rows)} продуктов", flush=True)
        upsert_rows(all_rows)
        upsert_discount_category_rows(all_category_rows)
        print(f"  upserted {len(all_rows)} rows ({len(all_category_rows)} discount-category rows)", flush=True)

    print("\nБэкфилл завершён.")


if __name__ == "__main__":
    main()
