"""
Discovery-спайк: проверяем рискованные эндпоинты DodoIS API.
Запускать локально: python discovery.py
Показывает первый элемент ответа (или ошибку) для каждого эндпоинта.
"""
import json
import os
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token

load_dotenv()
COUNTRY = os.getenv("DODO_COUNTRY", "ru").lower()
API_BASE = f"https://api.dodois.io/dodopizza/{COUNTRY}"

with open("yakutsk_units.json", encoding="utf-8") as f:
    UNITS = json.load(f)

# Берём одну пиццерию для теста
UNIT = UNITS[0]
UNIT_ID = UNIT["id"]
UNIT_NAME = UNIT["name"]

TODAY = date.today()
FROM_DATE = (TODAY - timedelta(days=7)).isoformat()
TO_DATE = (TODAY - timedelta(days=1)).isoformat()


def probe(label, url, params, token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    print(f"\n{'='*60}")
    print(f"[{label}]")
    print(f"  URL: {url}")
    print(f"  Params: {params}")
    try:
        r = httpx.get(url, headers=headers, params=params, timeout=30.0)
        print(f"  Статус: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            # Показываем ключи верхнего уровня и первый элемент списка
            if isinstance(data, dict):
                print(f"  Ключи: {list(data.keys())}")
                for key, val in data.items():
                    if isinstance(val, list) and val:
                        print(f"  [{key}][0]: {json.dumps(val[0], ensure_ascii=False, indent=4)}")
                    elif isinstance(val, list):
                        print(f"  [{key}]: пустой список")
                    else:
                        print(f"  {key}: {val}")
            elif isinstance(data, list):
                print(f"  Список, элементов: {len(data)}")
                if data:
                    print(f"  [0]: {json.dumps(data[0], ensure_ascii=False, indent=4)}")
        else:
            print(f"  Ошибка: {r.text[:300]}")
    except Exception as e:
        print(f"  ИСКЛЮЧЕНИЕ: {e}")


def fetch_swagger(token):
    """Пробуем получить список доступных эндпоинтов через Swagger."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    candidates = [
        "https://api.dodois.io/dodopizza/swagger/v1/swagger.json",
        "https://api.dodois.io/swagger/v1/swagger.json",
        "https://api.dodois.io/dodopizza/swagger.json",
    ]
    for url in candidates:
        try:
            r = httpx.get(url, headers=headers, timeout=15.0)
            if r.status_code == 200:
                spec = r.json()
                paths = list(spec.get("paths", {}).keys())
                print(f"\n  Swagger найден: {url}")
                print(f"  Всего эндпоинтов: {len(paths)}")
                print("  Пути:")
                for p in sorted(paths):
                    print(f"    {p}")
                return
        except Exception:
            pass
    print("  Swagger недоступен ни по одному из адресов.")


def main():
    print("Получаю токен...")
    token = get_access_token()
    print(f"Токен получен. Тестирую пиццерию: {UNIT_NAME} ({UNIT_ID})")
    print(f"Период: {FROM_DATE} — {TO_DATE}")

    p = {"units": UNIT_ID, "from": FROM_DATE, "to": TO_DATE}
    p_fd = {"units": UNIT_ID, "fromDate": FROM_DATE, "toDate": TO_DATE}

    # --- Доставка ---
    probe("Доставка: статистика", f"{API_BASE}/delivery/statistics", p, token)
    probe("Доставка: сертификаты за опоздание", f"{API_BASE}/delivery/vouchers", p, token)
    probe("Доставка: заказы курьеров", f"{API_BASE}/delivery/couriers-orders", p, token)
    probe("Доставка: стопы по секторам", f"{API_BASE}/delivery/stop-sales-sectors", p, token)

    # --- Производство (доставка + ресторан + стопы) ---
    probe("Производство: время выдачи заказа", f"{API_BASE}/production/orders-handover-time", p, token)
    probe("Производство: статистика выдачи заказов", f"{API_BASE}/production/orders-handover-statistics", p, token)
    probe("Производство: метрики трекинга (сводные)", f"{API_BASE}/production/tracking-metrics/summary", p, token)
    probe("Производство: производительность", f"{API_BASE}/production/productivity", p, token)
    probe("Производство: стопы по каналам", f"{API_BASE}/production/stop-sales-channels", p, token)
    probe("Производство: стопы по ингредиентам", f"{API_BASE}/production/stop-sales-ingredients", p, token)
    probe("Производство: стопы по продуктам", f"{API_BASE}/production/stop-sales-products", p, token)

    # --- Команда ---
    probe("Команда: список сотрудников", f"{API_BASE}/staff/members", {"units": UNIT_ID}, token)
    probe("Команда: смены по пиццерии", f"{API_BASE}/staff/shifts", p, token)
    probe("Команда: история должностей", f"{API_BASE}/staff/positions/history", p, token)
    probe("Команда: вознаграждения", f"{API_BASE}/staff/incentives-by-members", p, token)

    # --- Заказы / клиенты ---
    probe("Заказы: статистика новых клиентов", f"{API_BASE}/orders/clients-statistics", p, token)

    # --- Учет: себестоимость ---
    probe("Учет: списанные продукты", f"{API_BASE}/accounting/write-offs/products", p, token)
    probe("Учет: списанное сырьё", f"{API_BASE}/accounting/write-offs/stock-items", p, token)
    probe("Учет: забракованные продукты", f"{API_BASE}/accounting/defective-products", p, token)
    probe("Учет: продажи (с разбивкой)", f"{API_BASE}/accounting/sales", p, token)
    probe("Учет: отмены заказов", f"{API_BASE}/accounting/cancelled-sales", p, token)

    # --- Заведения ---
    probe("Заведения: цели на месяц", f"{API_BASE}/units/month-goals", {"units": UNIT_ID}, token)

    print(f"\n{'='*60}")
    print("Discovery завершён.")


if __name__ == "__main__":
    main()
