import os
import json

import httpx
from dotenv import load_dotenv

# --- 1. Загружаем секреты ---
load_dotenv()
COUNTRY = os.getenv("DODO_COUNTRY", "ru").lower()

# Константы из документации Dodo
API_BASE = "https://api.dodois.io/dodopizza"
BUSINESS_ID_PIZZA = "63d4829611ea45c8ae71394860a2481c"  # UUID бизнеса "Пицца"
TOKENS_FILE = "tokens.json"


# --- 2. Берём access_token из tokens.json ---
def load_access_token():
    if not os.path.exists(TOKENS_FILE):
        raise FileNotFoundError(
            "tokens.json не найден. Сначала запустите get_sales.py для авторизации."
        )
    with open(TOKENS_FILE, "r") as f:
        tokens = json.load(f)
    return tokens["access_token"]


# --- 3. Тянем список пиццерий с пагинацией ---
def fetch_all_stores(access_token):
    url = f"{API_BASE}/{COUNTRY}/units/stores"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    all_stores = []
    skip = 0
    take = 100

    while True:
        params = {
            "countryId": COUNTRY,
            "businessId": BUSINESS_ID_PIZZA,
            "skip": skip,
            "take": take,
        }
        print(f"Запрашиваю записи {skip}..{skip + take}...")
        response = httpx.get(url, headers=headers, params=params, timeout=30.0)

        # Отладка на случай ошибок
        if response.status_code != 200:
            print("Статус:", response.status_code)
            print("Тело:", response.text)
        response.raise_for_status()

        data = response.json()
        stores = data.get("stores", [])
        all_stores.extend(stores)

        if data.get("isEndOfListReached", True):
            break

        skip += take

    return all_stores


# --- 4. Главная функция ---
def main():
    print("Загружаю токен...")
    token = load_access_token()

    print("Тяну список пиццерий...")
    stores = fetch_all_stores(token)

    print(f"\nВсего пиццерий в сети: {len(stores)}")

    # --- РЕЖИМ РАЗВЕДКИ: смотрим на полную структуру первой пиццерии ---
    print("\n=== ПОЛНАЯ СТРУКТУРА ПЕРВОЙ ПИЦЦЕРИИ ===")
    print(json.dumps(stores[0], ensure_ascii=False, indent=2))
    print("=" * 50)

    # --- Печатаем краткую инфу по всем пиццериям, где встречается "Якутск" ---
    print("\n=== ПИЦЦЕРИИ, ГДЕ ГДЕ-ТО ВСТРЕЧАЕТСЯ 'ЯКУТСК' ===")
    found_count = 0
    for store in stores:
        name = store.get("name", "")
        location = store.get("location", {})
        locality = location.get("locality", "") if isinstance(location, dict) else ""
        region = location.get("region", "") if isinstance(location, dict) else ""
        org_name = store.get("organizationName", "") or ""

        # Склеиваем все текстовые поля в одну строку и ищем "якутск"
        full_text = f"{name} {locality} {region} {org_name}".lower()
        if "якутск" in full_text:
            found_count += 1
            print(f"  name='{name}' | locality='{locality}' | region='{region}' | org='{org_name}' | id={store.get('id')}")

    print(f"\nНайдено упоминаний 'Якутск': {found_count}")

    # --- Сохраняем ВСЕ данные в файл, чтобы потом можно было разбираться без повторных запросов к API ---
    with open("all_stores.json", "w", encoding="utf-8") as f:
        json.dump(stores, f, ensure_ascii=False, indent=2)
    print(f"\nВсе пиццерии сохранены в all_stores.json (можно открыть в Блокноте)")


if __name__ == "__main__":
    main()