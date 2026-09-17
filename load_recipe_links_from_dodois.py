# -*- coding: utf-8 -*-
# Достаёт связи «ингредиент → продукт» из выгрузки Dodo IS
# «Stops of products details» (Superset, dashboard 111) и дописывает их в
# product_recipe с source='dodois-export'.
#
# ЗАЧЕМ. Метрика Dodo IS «Упущенная выручка по стопам продуктов» считается от
# стопов ИНГРЕДИЕНТОВ, развёрнутых на затронутые продукты (выяснено 09.09.2026,
# см. docs/stops-lost-revenue-reconciliation.md). Значит нам нужна та же карта
# «ингредиент → продукты». Единственный наш источник такой карты — ТТК-файлы, а
# они дают её через fuzzy-match названий и систематически цепляют неторгуемого
# однофамильца: из 252 продуктов в product_recipe только 58 были в
# product_daily_revenue, у остальных база выручки пуста и упущенная выручка по
# стопу выходила РОВНО НОЛЬ.
#
# А в этой выгрузке ProductUUid приходит готовым — сопоставлять названия не
# надо вообще. Из 309 её продуктов 308 есть в product_daily_revenue.
#
# ЧЕГО ОЖИДАТЬ. Выгрузка покрывает только те ингредиенты, по которым в её окне
# были стопы. Загрузчик инкрементальный: новая выгрузка за новый период
# дописывает новые связи. Полной рецептуры он не даёт и не должен — он даёт
# ровно те связи, которые участвуют в метрике.
#
# ЧТО НЕ ДЕЛАЕТ. Не трогает существующие строки: если пара уже есть (из ТТК с
# нетто-граммами), она сохраняется как есть. Не удаляет ошибочные ТТК-связи —
# они дают ноль и потому инертны, но при желании их видно во вью
# product_recipe_without_revenue.
#
# Запуск:
#   python load_recipe_links_from_dodois.py "путь/к/Stops_of_products_details....csv"
import argparse
import re
import sys
from collections import defaultdict

import psycopg2.extras

from db import get_connection, get_cursor

EXPECTED_COLUMNS = 9
COL_INGREDIENT = 1
COL_PRODUCT = 2
COL_PRODUCT_UUID = 6


def norm(name: str) -> str:
    s = str(name).lower().replace("ё", "е")
    s = re.sub(r"[«»\"']", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def read_export(path: str):
    """(нормализованное имя ингредиента, product_uuid) -> имя продукта."""
    import csv

    pairs = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        if len(header) != EXPECTED_COLUMNS:
            sys.exit(
                f"Ожидались {EXPECTED_COLUMNS} колонок, получено {len(header)}: {header}.\n"
                "Это не та выгрузка — нужна «Stops of products details» с колонками "
                "Пиццерия/Наименование ингредиента/Продукт/…/ProductUUid/…"
            )
        for row in reader:
            if len(row) < EXPECTED_COLUMNS:
                continue
            ing = norm(row[COL_INGREDIENT])
            pu = row[COL_PRODUCT_UUID].strip().lower()
            if ing and pu:
                pairs[(ing, pu)] = row[COL_PRODUCT].strip()
    return pairs


def load_material_index(cur):
    """нормализованное имя ингредиента -> множество material_uuid.

    Имена берём из самих стопов: именно там лежит то написание, которое Dodo IS
    использует в выгрузке (проверено — 61 из 61 совпал точно, без fuzzy).
    material_classification идёт вторым источником для ингредиентов, которые в
    наших стопах ещё не встречались.
    """
    index = defaultdict(set)
    cur.execute(
        """
        SELECT DISTINCT entity_id, entity_name
        FROM stop_events
        WHERE event_type = 'ingredient' AND entity_id IS NOT NULL AND entity_name IS NOT NULL
        """
    )
    for r in cur.fetchall():
        index[norm(r["entity_name"])].add(r["entity_id"])
    cur.execute("SELECT material_uuid, material_name FROM material_classification WHERE material_name IS NOT NULL")
    for r in cur.fetchall():
        index[norm(r["material_name"])].add(r["material_uuid"])
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export_csv", help="CSV выгрузки «Stops of products details»")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать, ничего не писать")
    args = parser.parse_args()

    pairs = read_export(args.export_csv)
    ingredients = {ing for ing, _ in pairs}
    print(f"В выгрузке: {len(pairs)} пар (ингредиент, продукт), ингредиентов {len(ingredients)}")

    conn = get_connection()
    cur = get_cursor(conn)

    mat_index = load_material_index(cur)
    cur.execute("SELECT product_uuid FROM product_classification")
    known_products = {r["product_uuid"].lower() for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT product_id FROM product_daily_revenue")
    with_revenue = {r["product_id"].lower() for r in cur.fetchall()}
    cur.execute("SELECT product_uuid, material_uuid FROM product_recipe")
    existing = {(r["material_uuid"], r["product_uuid"].lower()) for r in cur.fetchall()}

    rows, unmatched_ing, unknown_product = [], set(), set()
    seen = set()
    for (ing, pu), product_name in pairs.items():
        mats = mat_index.get(ing)
        if not mats:
            unmatched_ing.add(ing)
            continue
        if pu not in known_products:
            unknown_product.add((product_name, pu))
            continue
        for mu in mats:
            key = (mu, pu)
            if key in existing or key in seen:
                continue
            seen.add(key)
            rows.append((pu, mu))

    print(f"Ингредиентов не найдено у нас: {len(unmatched_ing)}")
    for x in sorted(unmatched_ing)[:20]:
        print(f"    {x}")
    if unknown_product:
        print(f"Продуктов нет в product_classification: {len(unknown_product)}")
        for name, pu in sorted(unknown_product)[:10]:
            print(f"    {pu}  {name}")

    ambiguous = {ing: len(mat_index[ing]) for ing in ingredients if len(mat_index.get(ing, ())) > 1}
    if ambiguous:
        print(f"Имён ингредиента с несколькими material_uuid: {len(ambiguous)} "
              "(связи пишутся для всех — это дубли одного материала в справочниках)")

    new_with_revenue = len([1 for pu, _ in rows if pu in with_revenue])
    print(f"НОВЫХ связей: {len(rows)}, из них продукт есть в product_daily_revenue: {new_with_revenue}")
    if len(rows) and new_with_revenue / len(rows) < 0.8:
        print("⚠️  Меньше 80% новых связей ведут на продукт с выручкой — проверьте выгрузку, "
              "прежде чем доверять результату: без выручки связь даёт упущенную выручку 0.")

    if args.dry_run:
        print("--dry-run: ничего не записано")
        conn.close()
        return
    if not rows:
        print("Нечего дописывать")
        conn.close()
        return

    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO product_recipe (product_uuid, material_uuid, netto_grams, source) VALUES %s "
        # Существующую строку не трогаем: у ТТК-связи есть нетто-граммы, у нашей нет.
        "ON CONFLICT (product_uuid, material_uuid) DO NOTHING",
        rows,
        template="(%s, %s, NULL, 'dodois-export')",
        page_size=1000,
    )
    conn.commit()

    cur.execute("SELECT source, COUNT(*) n FROM product_recipe GROUP BY source ORDER BY source")
    print("product_recipe по источникам:")
    for r in cur.fetchall():
        print(f"    {r['source']:<16}{r['n']}")
    cur.execute("SELECT COUNT(*) n FROM product_recipe_without_revenue")
    print(f"связей без выручки продукта (дают ноль): {cur.fetchall()[0]['n']}")
    conn.close()
    print(f"Записано {len(rows)} связей.")


if __name__ == "__main__":
    main()
