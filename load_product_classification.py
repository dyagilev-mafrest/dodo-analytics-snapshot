# Loads the manually-exported "Subcategory dict" csv (from Dodo BI / Controlling,
# "Справочник ингредиентов и продуктов" tab of the "Стопы продуктов и ингредиентов"
# dashboard) into product_classification. Not an API pull — accounting/catalogs/stock-items
# returns 403 with our current OAuth scope — no scheduled job; re-run manually whenever
# a fresh export is provided.
#
# Expected columns (semicolon-delimited): CountryId, MetaProductUUId, MetaProductName,
#   ProductUUId, CategoryId, CategoryNameRus, ProductName, ProductSubcategory,
#   ClassificationNew, Breakfast, NumberOfProductIn21Days
#
# Also accepts an .xlsx export (e.g. the combined "DP_sync Product and Ingredient
# classification" workbook's "product_with_subcategory" sheet) via load_rows_xlsx —
# same fields, just named ProductCategory/ProductClassification instead of
# CategoryNameRus/ClassificationNew there.
import csv
import sys
import json
import os
from datetime import date

from dotenv import load_dotenv
from sheets_retry import retry_google

load_dotenv()


def load_rows(path: str, country: str = "ru") -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            if country and r["CountryId"] != country:
                continue
            product_uuid = (r.get("ProductUUId") or "").strip()
            if not product_uuid:
                continue
            consumption = r.get("NumberOfProductIn21Days") or None
            rows.append({
                "product_uuid": product_uuid.lower(),
                "meta_product_name": r.get("MetaProductName") or None,
                "category_id": int(r["CategoryId"]) if r.get("CategoryId") else None,
                "category_name": r.get("CategoryNameRus") or None,
                "product_name": r.get("ProductName") or None,
                "subcategory": r.get("ProductSubcategory") or None,
                "classification": r.get("ClassificationNew") or None,
                "is_breakfast": (r.get("Breakfast") or "").strip().lower() in ("true", "1", "yes"),
                "consumption_21d": float(consumption) if consumption else None,
                "uploaded_at": date.today(),
            })
    return rows


GSHEET_ID = "1FRM26JZ8CXGA14cszAIF-caSyMP2SgY8wVDJugO05qs"  # «DP_sync Product and Ingredient classification»
GSHEET_TAB = "product_with_subcategory"


def rows_from_records(records, country: str = "ru") -> list[dict]:
    """records — строки выгрузки как словари {заголовок: значение}.

    Источник (xlsx или Google Sheets) значения не меняет: колонки в обоих одинаковые.

    В выгрузке одна строка на (продукт x мета-продукт/комбо), поэтому один ProductUUId
    повторяется, а PK нашей таблицы — только product_uuid. ~99.5% дублей побайтово
    одинаковы, остальные чуть расходятся (категория/классификация) — берём последний
    попавшийся: осмысленного способа выбрать между ними нет, а количество мизерное
    (63 из 13931 продуктов на момент написания).
    """
    def get(r, *names):
        for name in names:
            if name in r and r[name] not in (None, ""):
                return r[name]
        return None

    by_uuid: dict[str, dict] = {}
    for r in records:
        if country and str(r.get("CountryId") or "").strip().lower() != country:
            continue
        product_uuid = str(get(r, "ProductUUId") or "").strip()
        if not product_uuid:
            continue
        category_id = get(r, "CategoryId")
        consumption = get(r, "NumberOfProductIn21Days")
        breakfast = get(r, "Breakfast")
        by_uuid[product_uuid.lower()] = {
            "product_uuid": product_uuid.lower(),
            "meta_product_name": get(r, "MetaProductName"),
            "category_id": int(category_id) if category_id else None,
            "category_name": get(r, "ProductCategory", "CategoryNameRus"),
            "product_name": get(r, "ProductName"),
            "subcategory": get(r, "ProductSubcategory"),
            "classification": get(r, "ProductClassification", "ClassificationNew"),
            "is_breakfast": str(breakfast or "").strip().lower() in ("true", "1", "yes"),
            "consumption_21d": _num(consumption),
            "uploaded_at": date.today(),
        }
    return list(by_uuid.values())


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def load_rows_xlsx(path: str, country: str = "ru", sheet: str = GSHEET_TAB) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    header = [h for h in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    records = [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True)]
    return rows_from_records(records, country)


def load_rows_gsheet(country: str = "ru", spreadsheet_id: str = GSHEET_ID,
                     tab: str = GSHEET_TAB) -> list[dict]:
    """Читает справочник прямо из гугл-таблицы «DP_sync Product and Ingredient
    classification» — той самой, которую команда контроллинга обновляет вручную
    выгрузками из дашборда. Убирает шаг «скачать файл и передать путь скрипту»,
    но свежесть по-прежнему определяется тем, когда её последний раз обновили."""
    import gspread
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    gc = gspread.service_account_from_dict(json.loads(sa_json))
    values = retry_google(
        lambda: gc.open_by_key(spreadsheet_id).worksheet(tab).get_all_values(),
        what=f"чтение листа «{tab}»",
    )
    if not values:
        return []
    header = values[0]
    records = [dict(zip(header, row)) for row in values[1:]]
    return rows_from_records(records, country)


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    from db import get_connection, get_cursor
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO product_classification
            (product_uuid, meta_product_name, category_id, category_name, product_name,
             subcategory, classification, is_breakfast, consumption_21d, uploaded_at)
        VALUES %s
        ON CONFLICT (product_uuid) DO UPDATE SET
            meta_product_name = EXCLUDED.meta_product_name,
            category_id        = EXCLUDED.category_id,
            category_name      = EXCLUDED.category_name,
            product_name        = EXCLUDED.product_name,
            subcategory        = EXCLUDED.subcategory,
            classification     = EXCLUDED.classification,
            is_breakfast        = EXCLUDED.is_breakfast,
            consumption_21d    = EXCLUDED.consumption_21d,
            uploaded_at         = EXCLUDED.uploaded_at
    """, [
        (
            row["product_uuid"], row["meta_product_name"], row["category_id"], row["category_name"],
            row["product_name"], row["subcategory"], row["classification"], row["is_breakfast"],
            row["consumption_21d"], row["uploaded_at"],
        )
        for row in rows
    ], page_size=500)
    conn.commit()
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="путь к csv/xlsx; не нужен с --gsheet")
    parser.add_argument("--gsheet", action="store_true", help="читать из гугл-таблицы DP_sync")
    parser.add_argument("--country", default="ru")
    parser.add_argument("--sheet", default="product_with_subcategory", help="sheet name, only used for .xlsx input")
    args = parser.parse_args()

    if args.gsheet:
        rows = load_rows_gsheet(args.country, tab=args.sheet)
    elif not args.path:
        parser.error("нужен путь к файлу или --gsheet")
    elif args.path.lower().endswith(".xlsx"):
        rows = load_rows_xlsx(args.path, args.country, args.sheet)
    else:
        rows = load_rows(args.path, args.country)
    print(f"Строк для загрузки: {len(rows)}")

    # Свежесть проверяем ДО записи: upsert по ключу, и устаревший источник
    # затёр бы свежие строки ещё до того, как SCD-2 успела бы возразить.
    from classification_history import (append_snapshot, assert_snapshot_not_older,
                                    print_summary, warn_if_stale)
    snapshot_date = max(r["uploaded_at"] for r in rows) if rows else date.today()
    assert_snapshot_not_older("product_classification", snapshot_date)

    upsert_rows(rows)

    # Справочник Dodo IS — скользящий снимок (окно «расход за 21 день»), статусы
    # мигрируют. Плоская таблица выше держит только последний снимок, поэтому
    # параллельно копим историю: без неё нельзя узнать статус продукта на дату
    # прошлого стопа, а метрика «Стопы ключевых ингредиентов» именно его и требует.
    stats = append_snapshot(
        "product_classification", "product_uuid", rows,
        ["classification", "category_name", "product_name"], snapshot_date,
    )
    print_summary("продуктов", stats, snapshot_date)
    warn_if_stale("product_classification", snapshot_date)
    print("Готово.")


if __name__ == "__main__":
    main()
