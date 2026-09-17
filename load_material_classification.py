# Loads the manually-exported "Dictionary of the materials" (from Dodo BI /
# Controlling) into material_classification. Not an API pull — no scheduled job;
# re-run manually whenever a fresh export is provided.
#
# Two known export shapes:
#   - .xlsx, positional columns: Country Id, Material UUId, Категория,
#     Категория материала, RatingCategory, Классификация, Расход за 21 день, Дата выгрузки
#   - .csv (semicolon-delimited, named header — seen 2026-08-31, "Materials
#     Classification Dictionary" Superset export): CountryId;MaterialUUId;Name;
#     Category;Rating;ClassificationNew;TotalConsumptionIn21Days. `Category` here is
#     a numeric code (1/2/3/5...), not the textual material_category from the xlsx
#     shape — stored as-is (not used by the "key ingredient" filter, only
#     `classification` is).
import csv
import json
import os
import sys
from datetime import date

import openpyxl

from db import get_connection, get_cursor
from sheets_retry import retry_google


def load_rows_csv(path: str, country: str = "ru") -> list[dict]:
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for r in reader:
            if country and (r.get("CountryId") or "").strip().lower() != country:
                continue
            material_uuid = (r.get("MaterialUUId") or "").strip()
            if not material_uuid:
                continue
            consumption = r.get("TotalConsumptionIn21Days") or None
            rating = r.get("Rating") or None
            rows.append({
                "material_uuid": material_uuid.lower(),
                "material_name": r.get("Name") or None,
                "material_category": r.get("Category") or None,
                "rating_category": int(float(rating)) if rating else None,
                "classification": r.get("ClassificationNew") or None,
                "consumption_21d": float(consumption) if consumption else None,
                "uploaded_at": date.today(),
            })
    return rows


def load_rows(path: str, country: str = "ru", sheet: str = "Sheet1") -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        country_id, material_uuid, name, material_category, rating_category, classification, consumption, uploaded_at = r[:8]
        if not material_uuid or (country and country_id != country):
            continue
        rows.append({
            "material_uuid": material_uuid.lower(),
            "material_name": name,
            "material_category": material_category,
            "rating_category": rating_category,
            "classification": classification,
            "consumption_21d": consumption,
            "uploaded_at": uploaded_at.date() if hasattr(uploaded_at, "date") else uploaded_at,
        })
    return rows


GSHEET_ID = "1FRM26JZ8CXGA14cszAIF-caSyMP2SgY8wVDJugO05qs"  # «DP_sync Product and Ingredient classification»
GSHEET_TAB = "ingredients"


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def load_rows_gsheet(country: str = "ru", spreadsheet_id: str = GSHEET_ID,
                     tab: str = GSHEET_TAB) -> list[dict]:
    """Справочник материалов из гугл-таблицы «DP_sync Product and Ingredient classification».

    ВНИМАНИЕ: вкладку `ingredients` в этой таблице обновляют вручную, и на 01.09.2026
    она отставала на 19 месяцев («Дата выгрузки» = 2025-02-02, 234 строки по ru против
    637 в нашей БД от 31.08.2026). Поэтому дату снимка берём из самой таблицы, а не
    подставляем сегодняшнюю: SCD-2 в classification_history отвергнет снимок старее
    уже загруженного, и устаревшая вкладка не сможет откатить историю назад.
    Как только вкладку обновят — загрузка начнёт работать сама, без правок кода.
    """
    import gspread
    from dotenv import load_dotenv
    load_dotenv()
    gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    values = retry_google(
        lambda: gc.open_by_key(spreadsheet_id).worksheet(tab).get_all_values(),
        what=f"чтение листа «{tab}»",
    )
    if not values:
        return []
    header = values[0]
    rows = []
    for raw in values[1:]:
        r = dict(zip(header, raw))
        if country and str(r.get("CountryId") or "").strip().lower() != country:
            continue
        material_uuid = str(r.get("MaterialUUId") or "").strip()
        if not material_uuid:
            continue
        exported = str(r.get("Дата выгрузки") or "").strip()[:10]
        rating = _num(r.get("RatingCategory"))
        rows.append({
            "material_uuid": material_uuid.lower(),
            "material_name": r.get("Name") or None,
            "material_category": r.get("MaterialCategory") or r.get("Category") or None,
            "rating_category": int(rating) if rating else None,
            "classification": r.get("ClassificationMaterial") or r.get("ClassificationNew") or None,
            "consumption_21d": _num(r.get("Расход за 21 день") or r.get("TotalConsumptionIn21Days")),
            "uploaded_at": date.fromisoformat(exported) if exported else date.today(),
        })
    return rows


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO material_classification
            (material_uuid, material_name, material_category, rating_category, classification, consumption_21d, uploaded_at)
        VALUES %s
        ON CONFLICT (material_uuid) DO UPDATE SET
            material_name     = EXCLUDED.material_name,
            material_category = EXCLUDED.material_category,
            rating_category   = EXCLUDED.rating_category,
            classification    = EXCLUDED.classification,
            consumption_21d   = EXCLUDED.consumption_21d,
            uploaded_at        = EXCLUDED.uploaded_at
    """, [
        (
            row["material_uuid"], row["material_name"], row["material_category"],
            row["rating_category"], row["classification"], row["consumption_21d"], row["uploaded_at"],
        )
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="путь к csv/xlsx; не нужен с --gsheet")
    parser.add_argument("--gsheet", action="store_true", help="читать из гугл-таблицы DP_sync")
    parser.add_argument("--country", default="ru")
    parser.add_argument("--sheet", default="Sheet1", help="sheet name, only used for .xlsx input")
    args = parser.parse_args()

    if args.gsheet:
        rows = load_rows_gsheet(args.country)
    elif not args.path:
        parser.error("нужен путь к файлу или --gsheet")
    elif args.path.lower().endswith(".csv"):
        rows = load_rows_csv(args.path, args.country)
    else:
        rows = load_rows(args.path, args.country, args.sheet)
    print(f"Строк для загрузки: {len(rows)}")

    # Свежесть проверяем ДО записи: upsert по ключу, и устаревший источник
    # затёр бы свежие строки ещё до того, как SCD-2 успела бы возразить.
    from classification_history import (append_snapshot, assert_snapshot_not_older,
                                    print_summary, warn_if_stale)
    snapshot_date = max(r["uploaded_at"] for r in rows) if rows else date.today()
    assert_snapshot_not_older("material_classification", snapshot_date)

    upsert_rows(rows)

    # См. комментарий в load_product_classification.py: справочник скользящий,
    # «Категория А» у материала на прошлой неделе и сегодня — разные вещи.
    stats = append_snapshot(
        "material_classification", "material_uuid", rows,
        ["classification", "material_name"], snapshot_date,
    )
    print_summary("материалов", stats, snapshot_date)
    warn_if_stale("material_classification", snapshot_date)
    print("Готово.")


if __name__ == "__main__":
    main()
