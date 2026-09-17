# Загружает полный справочник сырья/товаров из Dodo IS Accounting API
# (GET /accounting/stock-items) в таблицу stock_items_catalog. Ручной запуск,
# переоткрывать по мере необходимости (справочник обновляется на стороне Dodo IS
# нечасто — modified_at в ответе позволит в будущем сделать инкрементальный догруз,
# сейчас делаем полный оверрайт).
#
# Найдено 2026-08-31 реверс-инжинирингом открытого проекта 2mozg-oss
# (github.com/SergGT/2mozg-oss, Apache-2.0) — у него было рабочее обращение к этому
# эндпоинту с явно указанным scope accounting/stockitems; проверка показала, что наш
# существующий OAuth-токен (device flow, client dn24e) уже имеет доступ, без
# дополнительной регистрации. См. migrations/051_stock_items_catalog.sql, issue #6.
import json

import psycopg2.extras
import requests

from db import get_connection, get_cursor
from get_sales import get_access_token

API_BASE = "https://api.dodois.io/dodopizza/RU"


def fetch_all_stock_items(unit_ids: list[str]) -> list[dict]:
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    unit_param = ",".join(unit_ids)
    items = []
    skip, take = 0, 1000
    while True:
        r = requests.get(
            f"{API_BASE}/accounting/stock-items",
            headers=headers,
            params={"units": unit_param, "skip": skip, "take": take},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        chunk = data.get("stockItems", [])
        items.extend(chunk)
        print(f"  skip={skip}: {len(chunk)} строк, всего {len(items)}")
        if len(chunk) < take:
            break
        skip += take
    return items


def main():
    with open("yakutsk_units.json", encoding="utf-8") as f:
        units = json.load(f)
    unit_ids = [u["id"] for u in units]

    print(f"Тяну accounting/stock-items по {len(unit_ids)} юнитам...")
    items = fetch_all_stock_items(unit_ids)
    print(f"\nВсего получено: {len(items)} позиций")

    by_category = {}
    for it in items:
        cat = it.get("categoryName") or "?"
        by_category[cat] = by_category.get(cat, 0) + 1
    print("По категориям:", by_category)

    rows = []
    seen = set()
    for it in items:
        item_id = it.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        rows.append((
            item_id,
            it.get("name"),
            it.get("categoryName"),
            it.get("measurementUnit"),
            it.get("modifiedAt"),
        ))

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("TRUNCATE TABLE stock_items_catalog")
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO stock_items_catalog (stock_item_id, name, category_name, measurement_unit, modified_at) VALUES %s",
        rows,
        template="(%s, %s, %s, %s, %s)",
        page_size=1000,
    )
    conn.commit()
    conn.close()
    print(f"\nЗаписано {len(rows)} уникальных строк в stock_items_catalog.")


if __name__ == "__main__":
    main()
