# One-off diagnostic: calibrate which ingredient classification(s) count as
# "ключевые ингредиенты" by comparing our computed lost_revenue totals (summed
# over product-stops attributed to an ingredient-stop via unit+reason+time
# matching) against a known ground-truth figure for a given week.
#
# Not part of the daily pipeline — run manually, e.g.:
#   python analyze_key_ingredients_calibration.py --from-date 2026-06-29 --to-date 2026-07-05
import sys
from datetime import date

from db import get_connection, get_cursor


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    conn = get_connection()
    cur = get_cursor(conn)

    cur.execute("SELECT material_uuid, classification FROM material_classification")
    classification_by_uuid = {row["material_uuid"]: row["classification"] for row in cur.fetchall()}

    cur.execute(
        """
        SELECT id, unit_id, entity_id, reason, started_at_local
        FROM stop_events
        WHERE event_type = 'ingredient'
          AND started_at_local::date BETWEEN %s AND %s
        """,
        (args.from_date, args.to_date),
    )
    ingredient_stops = cur.fetchall()

    cur.execute(
        """
        SELECT id, unit_id, reason, started_at_local, lost_revenue
        FROM stop_events
        WHERE event_type = 'product'
          AND lost_revenue IS NOT NULL
          AND started_at_local::date BETWEEN %s AND %s
        """,
        (args.from_date, args.to_date),
    )
    product_stops = cur.fetchall()
    conn.close()

    print(f"Ingredient stops: {len(ingredient_stops)}, product stops with lost_revenue: {len(product_stops)}")

    # index ingredient stops by exact (unit, reason, started_at_local) and by (unit, reason, day)
    by_exact = {}
    by_day = {}
    for ing in ingredient_stops:
        classification = classification_by_uuid.get((ing["entity_id"] or "").lower(), "Не в справочнике")
        exact_key = (ing["unit_id"], ing["reason"], str(ing["started_at_local"]))
        day_key = (ing["unit_id"], ing["reason"], str(ing["started_at_local"])[:10])
        by_exact.setdefault(exact_key, set()).add(classification)
        by_day.setdefault(day_key, set()).add(classification)

    def matched_classifications(p, exact_only: bool):
        exact_key = (p["unit_id"], p["reason"], str(p["started_at_local"]))
        classes = set(by_exact.get(exact_key, set()))
        if not exact_only:
            day_key = (p["unit_id"], p["reason"], str(p["started_at_local"])[:10])
            classes |= by_day.get(day_key, set())
        return classes

    candidate_sets = {
        "Категория А": {"Категория А: Стратегические ингредиенты"},
        "Категория А+В": {"Категория А: Стратегические ингредиенты", "Категория В: Обязательные ингредиенты"},
        "А+В+С (все классифицированные)": {
            "Категория А: Стратегические ингредиенты",
            "Категория В: Обязательные ингредиенты",
            "Категория С: Прочие не критичные",
        },
        "Все ингредиенты (любая классификация, вкл. не в справочнике)": None,  # any match at all
    }

    for match_mode, exact_only in [("только точное совпадение unit+reason+время", True), ("+ fallback по дню", False)]:
        print(f"\n=== Режим сопоставления: {match_mode} ===")
        for label, target_classes in candidate_sets.items():
            total = 0.0
            matched_count = 0
            for p in product_stops:
                classes = matched_classifications(p, exact_only)
                if not classes:
                    continue
                if target_classes is None or (classes & target_classes):
                    total += float(p["lost_revenue"] or 0)
                    matched_count += 1
            print(f"  {label}: {total:.2f} руб ({matched_count} product-стопов)")

    print("\nЦель для сравнения: 92361 руб (упущенная выручка по стопам ключевых ингредиентов, пн-вс)")


if __name__ == "__main__":
    main()
