# Google Sheets -> PostgreSQL (Supabase): warehouse_stock, warehouse_receipts,
# warehouse_writeoffs, warehouse_shipments
#
# Sources (два разных Google-документа, оба расшарены с тем же сервисным
# аккаунтом):
# 1. "Движение ТМЦ" (владелец — закупки, zakupdodo22@gmail.com): "Остатки ТМЦ"
#    (весь склад, полный срез на каждую дату — снепшот по партиям, не история
#    изменений), "Поступления ТМЦ" (журнал приходов) и "Списание ТМЦ" (журнал
#    списаний). Три центральных склада: Мороз, Сухой, Холод.
# 2. "Реестр отгрузок" (ведёт транспортный логист): лист "Реестр отгрузок ОС" —
#    грузы от поставщиков до складов/пиццерий, со статусом (План/Отгружен в
#    ТК/В пути/Задерживается/Прибыл в пункт/Получен/Утерян/Отменен).
#
# Источник для блока «Склад и логистика» (запрошено пользователем 2026-08-12).
#
# Auth: тот же service account, что get_staffing_plan.py/get_hr_hiring_funnel.py
# (GOOGLE_SERVICE_ACCOUNT_JSON) — оба документа расшарены с его email отдельно.
#
# Все листы отдают полную историю целиком при каждом чтении -> полный
# TRUNCATE + INSERT на каждый запуск, не upsert (см. migrations/028, 029, 030).
import json
import os

import gspread
from dotenv import load_dotenv

from db import get_connection, get_cursor
from sheets_retry import retry_google

load_dotenv()
SPREADSHEET_ID = "1GXCWU8z1cSqCpPzawQCXAUUFLxvkoZegZQVTh1mvpjQ"
STOCK_SHEET = "Остатки ТМЦ"
RECEIPTS_SHEET = "Поступления ТМЦ"
WRITEOFFS_SHEET = "Списание ТМЦ"

SHIPMENTS_SPREADSHEET_ID = "1sO7gQ2QWT-_Zh5FzLgtOBHNAr54rhlNIMz3GKAFmDqs"
SHIPMENTS_SHEET = "Реестр отгрузок ОС"

ERROR_VALUES = {"#DIV/0!", "#REF!", "#N/A", "#VALUE!", "#NAME?"}


def parse_number(raw: str) -> float | None:
    s = (raw or "").strip().replace("\xa0", "").replace(" ", "")
    if not s or s in ERROR_VALUES:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        day, month, year = s.split(".")
        return f"{year}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return None


def fetch_sheet(gc: gspread.Client, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    # Google отдаёт 503 на ровном месте (03.09.2026 на этом упал Daily Warehouse
    # Load), поэтому и открытие документа, и чтение листа идут через повторы.
    sh = retry_google(lambda: gc.open_by_key(spreadsheet_id), what="открытие таблицы")
    return retry_google(
        lambda: sh.worksheet(sheet_name).get_all_values(),
        what=f"чтение листа «{sheet_name}»",
    )


def transform_stock(rows: list[list[str]]) -> list[dict]:
    out = []
    for row in rows[1:]:
        if len(row) < 8:
            continue
        date = parse_date(row[0])
        warehouse = row[1].strip()
        nomenclature = row[2].strip()
        if not date or not warehouse or not nomenclature:
            continue
        out.append({
            "date": date,
            "warehouse": warehouse,
            "nomenclature": nomenclature,
            "batch": row[3].strip() or None,
            "nomenclature_group": row[4].strip() or None,
            "unit": row[5].strip() or None,
            "quantity": parse_number(row[6]),
            "value_rub": parse_number(row[7]),
        })
    return out


def transform_receipts(rows: list[list[str]]) -> list[dict]:
    out = []
    for row in rows[1:]:
        if len(row) < 12:
            continue
        date = parse_date(row[0])
        warehouse = row[1].strip()
        nomenclature = row[3].strip()
        if not date or not warehouse or not nomenclature:
            continue
        out.append({
            "date": date,
            "warehouse": warehouse,
            "supplier": row[2].strip() or None,
            "nomenclature": nomenclature,
            "nomenclature_group": row[5].strip() or None,
            "unit": row[4].strip() or None,
            "quantity": parse_number(row[9]),
            "value_rub": parse_number(row[11]),
        })
    return out


def transform_writeoffs(rows: list[list[str]]) -> list[dict]:
    out = []
    for row in rows[1:]:
        if len(row) < 8:
            continue
        date = parse_date(row[0])
        nomenclature = row[1].strip()
        warehouse = row[3].strip()
        if not date or not nomenclature or not warehouse:
            continue
        out.append({
            "date": date,
            "warehouse": warehouse,
            "nomenclature": nomenclature,
            "batch": row[2].strip() or None,
            "comment": row[4].strip() or None,
            "quantity": parse_number(row[5]),
            "price": parse_number(row[6]),
            "amount_rub": parse_number(row[7]),
        })
    return out


def transform_shipments(rows: list[list[str]]) -> list[dict]:
    out = []
    for row in rows[1:]:
        if len(row) < 21:
            continue
        status = row[0].strip()
        if not status:
            continue
        out.append({
            "status": status,
            "planned_receipt_date": parse_date(row[1]),
            "shipped_date": parse_date(row[2]),
            "actual_receipt_date": parse_date(row[3]),
            "days_in_transit": parse_number(row[4]),
            "sender": row[5].strip() or None,
            "receiver": row[6].strip() or None,
            "order_number": row[7].strip() or None,
            "order_amount_rub": parse_number(row[8]),
            "payment_status": row[9].strip() or None,
            "departure_city": row[10].strip() or None,
            "carrier": row[11].strip() or None,
            "shipping_to_carrier": row[12].strip() or None,
            "shipping_method": row[13].strip() or None,
            "receiving_method": row[14].strip() or None,
            "temperature_mode": row[15].strip() or None,
            "destination_unit": row[16].strip() or None,
            "weight_kg": parse_number(row[17]),
            "volume_m3": parse_number(row[18]),
            "package_count": parse_number(row[19]),
            "pallet_count": parse_number(row[20]),
            "comment": row[21].strip() if len(row) > 21 else None,
        })
    return out


def reload_table(table: str, rows: list[dict], columns: list[str]):
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"TRUNCATE TABLE {table}")
    if rows:
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s",
            [tuple(r[c] for c in columns) for r in rows],
            page_size=1000,
        )
    conn.commit()
    conn.close()


def main():
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    gc = gspread.service_account_from_dict(json.loads(sa_json))

    print("Fetching Остатки ТМЦ...", flush=True)
    stock_rows = transform_stock(fetch_sheet(gc, SPREADSHEET_ID, STOCK_SHEET))
    print(f"  {len(stock_rows)} rows parsed", flush=True)
    reload_table(
        "warehouse_stock", stock_rows,
        ["date", "warehouse", "nomenclature", "nomenclature_group", "batch", "unit", "quantity", "value_rub"],
    )
    print(f"Reloaded warehouse_stock ({len(stock_rows)} rows)", flush=True)

    print("Fetching Поступления ТМЦ...", flush=True)
    receipt_rows = transform_receipts(fetch_sheet(gc, SPREADSHEET_ID, RECEIPTS_SHEET))
    print(f"  {len(receipt_rows)} rows parsed", flush=True)
    reload_table(
        "warehouse_receipts", receipt_rows,
        ["date", "warehouse", "supplier", "nomenclature", "nomenclature_group", "unit", "quantity", "value_rub"],
    )
    print(f"Reloaded warehouse_receipts ({len(receipt_rows)} rows)", flush=True)

    print("Fetching Списание ТМЦ...", flush=True)
    writeoff_rows = transform_writeoffs(fetch_sheet(gc, SPREADSHEET_ID, WRITEOFFS_SHEET))
    print(f"  {len(writeoff_rows)} rows parsed", flush=True)
    reload_table(
        "warehouse_writeoffs", writeoff_rows,
        ["date", "warehouse", "nomenclature", "batch", "comment", "quantity", "price", "amount_rub"],
    )
    print(f"Reloaded warehouse_writeoffs ({len(writeoff_rows)} rows)", flush=True)

    print("Fetching Реестр отгрузок ОС...", flush=True)
    shipment_rows = transform_shipments(fetch_sheet(gc, SHIPMENTS_SPREADSHEET_ID, SHIPMENTS_SHEET))
    print(f"  {len(shipment_rows)} rows parsed", flush=True)
    reload_table(
        "warehouse_shipments", shipment_rows,
        ["status", "planned_receipt_date", "shipped_date", "actual_receipt_date", "days_in_transit",
         "sender", "receiver", "order_number", "order_amount_rub", "payment_status", "departure_city",
         "carrier", "shipping_to_carrier", "shipping_method", "receiving_method", "temperature_mode",
         "destination_unit", "weight_kg", "volume_m3", "package_count", "pallet_count", "comment"],
    )
    print(f"Reloaded warehouse_shipments ({len(shipment_rows)} rows)", flush=True)


if __name__ == "__main__":
    main()
