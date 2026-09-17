# ingredient_stop_impact.lost_revenue — упущенная выручка каждого продукта, реально
# связанного (product_recipe, из ТТК) с ингредиентным стопом, посчитанная по формуле
# compute_lost_revenue.py, но с окном времени ИНГРЕДИЕНТНОГО стопа как триггером —
# не через поиск совпадающей по времени строки в product-стопах (см. миграцию
# 050_ingredient_stop_impact.sql и issue #6 за объяснением, почему).
#
# Один ингредиентный стоп -> N строк (по одной на каждый связанный продукт).
# Пересчитывает диапазон дат каждый раз с нуля (DELETE + INSERT для затронутых
# ингредиентных стопов) — идемпотентно, в отличие от compute_lost_revenue.py не нужно
# аккуратничать с накоплением, т.к. здесь нет двойного счёта между строками одной группы.
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import psycopg2.extras

from compute_lost_revenue import (
    MAX_LOOKBACK_WEEKS,
    compute_days_for_stop,
    load_long_stop_days,
    load_revenue_map,
    load_schedules,
)
from db import get_connection, get_cursor


def load_classification_history(cur, table: str, key_column: str):
    """key -> (отсортированный список (valid_from, valid_to, classification), earliest).

    Справочник Dodo IS пересобирается по скользящему окну «расход за 21 день», так что
    статус позиции меняется со временем (Тест → Новинка → Обязательный ассортимент →
    Распродажа). Метрика «Стопы ключевых ингредиентов» должна брать статус НА ДАТУ
    СТОПА — см. миграцию 052 и docs/stops-lost-revenue-reconciliation.md.
    """
    cur.execute(
        f"SELECT {key_column} AS k, valid_from, valid_to, classification FROM {table} ORDER BY {key_column}, valid_from"
    )
    hist = defaultdict(list)
    for r in cur.fetchall():
        hist[r["k"]].append((r["valid_from"], r["valid_to"], r["classification"]))
    return hist


def classification_as_of(hist: dict, key: str, day: date) -> tuple[str | None, bool]:
    """Возвращает (статус, точно_ли). Интервал полуоткрытый: [valid_from, valid_to).

    Если стоп случился раньше самой первой выгрузки справочника, честной истории для
    него не существует — берём самый ранний известный снимок и помечаем строку как
    неточную (`classification_exact = false`), чтобы приближение было видно в данных,
    а не растворилось в итоговой сумме.
    """
    versions = hist.get(key)
    if not versions:
        return None, False
    for valid_from, valid_to, value in versions:
        if valid_from <= day and (valid_to is None or day < valid_to):
            return value, True
    if day < versions[0][0]:
        return versions[0][2], False
    return versions[-1][2], False  # позиция выпала из выгрузки — берём последний статус


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="filters by started_at_local date")
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    schedules = load_schedules()
    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    conn = get_connection()
    cur = get_cursor(conn)

    # Незакрытые стопы тоже считаем — до «сейчас».
    #
    # Why: раньше здесь стояло `ended_at_local IS NOT NULL`, и упущенная выручка
    # по висящему стопу была не занижена, а РАВНА НУЛЮ. На свежей неделе это
    # обнуляет метрику: сверка 09.09.2026 показала 19% от цифры Dodo IS за
    # последнюю неделю против 81-112% за недели месячной давности. Самый
    # наглядный случай — Rich Tea Чёрный с лимоном: у Dodo IS 20 822 ₽ (11%
    # всей недельной суммы), у нас ноль, потому что оба стопа висели открытыми.
    #
    # Оценка по открытому стопу растёт с каждым прогоном и уточняется, когда
    # `refresh_open_stops.py` допишет `ended_at_local`. Это безопасно: скрипт
    # удаляет и пересчитывает строки затронутых стопов целиком (DELETE+INSERT),
    # так что двойного счёта не будет, а цифра всегда отражает простой «на
    # сегодня». В дашборде незакрытые стопы уже помечены амбер-баннером —
    # читатель предупреждён, что оценка дозреет.
    now_local = datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)  # Якутск UTC+9
    cur.execute(
        """
        SELECT id, unit_id, entity_id, started_at_local,
               COALESCE(ended_at_local, %s) AS ended_at_local,
               ended_at_local IS NULL AS is_open
        FROM stop_events
        WHERE event_type = 'ingredient'
          AND entity_id IS NOT NULL
          AND started_at_local::date BETWEEN %s AND %s
          AND started_at_local <= %s
        """,
        (now_local, args.from_date, args.to_date, now_local),
    )
    ingredient_stops = cur.fetchall()
    n_open = sum(1 for r in ingredient_stops if r["is_open"])
    print(f"Найдено ингредиентных стопов: {len(ingredient_stops)}"
          f" (из них незакрытых, считаем до {now_local:%Y-%m-%d %H:%M}: {n_open})")
    if not ingredient_stops:
        conn.close()
        return

    cur.execute("SELECT product_uuid, material_uuid FROM product_recipe")
    recipe_map = defaultdict(set)
    for r in cur.fetchall():
        recipe_map[r["material_uuid"]].add(r["product_uuid"])

    # (unit_id, product_uuid) пары, которые реально понадобятся — только те продукты,
    # что связаны рецептурой хоть с одним материалом из наших стопов, на всех юнитах,
    # где эти стопы встретились.
    unit_ids = {row["unit_id"] for row in ingredient_stops}
    needed_products = set()
    for row in ingredient_stops:
        needed_products |= recipe_map.get(row["entity_id"], set())
    print(f"Затронутых материалов с рецептурной связью: {len({r['entity_id'] for r in ingredient_stops if recipe_map.get(r['entity_id'])})}, продуктов: {len(needed_products)}")

    if not needed_products:
        print("Нет продуктов с рецептурной связью в этом диапазоне — нечего считать.")
        conn.close()
        return

    lookback_start = from_date - timedelta(weeks=MAX_LOOKBACK_WEEKS)
    print("Загружаю дни с длинными (>2ч) стопами и выручку одним запросом каждая...")
    long_stop_days = load_long_stop_days(cur, unit_ids, needed_products, lookback_start, to_date)
    revenue_map = load_revenue_map(cur, unit_ids, needed_products, lookback_start, to_date)
    print(f"  дней с длинным стопом (>2ч): {sum(len(v) for v in long_stop_days.values())}, строк выручки: {len(revenue_map)}")

    print("Загружаю историю классификаций...")
    mat_hist = load_classification_history(cur, "material_classification_history", "material_uuid")
    prod_hist = load_classification_history(cur, "product_classification_history", "product_uuid")
    print(f"  материалов в истории: {len(mat_hist)}, продуктов: {len(prod_hist)}")

    impact_rows = []
    n_stops_with_impact = 0
    n_approx = 0
    n_day_rows = 0
    for row in ingredient_stops:
        related = recipe_map.get(row["entity_id"])
        if not related:
            continue
        n_stops_with_impact += 1
        stop_day = row["started_at_local"].date()
        mat_class, mat_exact = classification_as_of(mat_hist, row["entity_id"], stop_day)
        for product_uuid in related:
            # Подневная раскладка, а не одно число на стоп: каждый день считается
            # от своей базы, поэтому у длинного стопа суммы по дням убывают —
            # так же, как в выгрузке Dodo IS (миграция 059).
            per_day = compute_days_for_stop(
                schedules, long_stop_days, revenue_map, row["unit_id"], product_uuid,
                row["started_at_local"], row["ended_at_local"],
            )
            if not per_day:
                continue
            prod_class, prod_exact = classification_as_of(prod_hist, product_uuid, stop_day)
            exact = mat_exact and prod_exact
            n_approx += not exact
            n_day_rows += len(per_day)
            for day, lost in per_day:
                impact_rows.append((row["id"], product_uuid, day, lost, mat_class, prod_class, exact))

    print(f"Ингредиентных стопов с рецептурной связью: {n_stops_with_impact}/{len(ingredient_stops)}")
    print(f"Строк impact собрано: {len(impact_rows)} (стоп-дней {n_day_rows}), "
          f"из них с приближённой классификацией: {n_approx}")

    stop_ids = [row["id"] for row in ingredient_stops]
    cur.execute("DELETE FROM ingredient_stop_impact WHERE stop_event_id = ANY(%s)", (stop_ids,))
    if impact_rows:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO ingredient_stop_impact "
            "(stop_event_id, product_uuid, stop_date, lost_revenue, material_classification, "
            " product_classification, classification_exact) VALUES %s "
            "ON CONFLICT (stop_event_id, product_uuid, stop_date) DO UPDATE SET "
            "  lost_revenue = EXCLUDED.lost_revenue,"
            "  material_classification = EXCLUDED.material_classification,"
            "  product_classification = EXCLUDED.product_classification,"
            "  classification_exact = EXCLUDED.classification_exact",
            impact_rows,
            template="(%s, %s, %s, %s, %s, %s, %s)",
            page_size=1000,
        )
    conn.commit()
    conn.close()
    print(f"Записано {len(impact_rows)} строк в ingredient_stop_impact.")


if __name__ == "__main__":
    main()
