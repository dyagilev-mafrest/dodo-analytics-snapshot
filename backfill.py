import json
import time
from datetime import date, timedelta

from db import init_db, save_daily_sales
from get_revenue import load_access_token, fetch_daily_sales

UNITS_FILE = "yakutsk_units.json"


def daterange(start_date, end_date, chunk_days=9):
    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end_date)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def get_unit_start_date(unit):
    dates = []
    if unit.get("restaurant_open_date"):
        dates.append(date.fromisoformat(unit["restaurant_open_date"]))
    if unit.get("delivery_open_date"):
        dates.append(date.fromisoformat(unit["delivery_open_date"]))
    return min(dates) if dates else date(2016, 1, 1)


def backfill_unit(token, unit_id, unit_name, start_date, end_date):
    chunks = list(daterange(start_date, end_date))
    total_days = (end_date - start_date).days + 1
    print(f"\n  [{unit_name}] {start_date} — {end_date}")
    print(f"  {total_days} дней → {len(chunks)} запросов")

    saved = 0
    empty = 0
    errors = 0

    for i, (from_date, to_date) in enumerate(chunks, 1):
        try:
            print(f"    [{i:>3}/{len(chunks)}] {from_date} — {to_date}...",
                  end=" ", flush=True)
            data = fetch_daily_sales(token, unit_id, from_date, to_date)
            result = data.get("result", [])

            if result:
                save_daily_sales(result)
                saved += len(result)
                print(f"OK ({len(result)} дней)")
            else:
                empty += 1
                print("пусто")

            time.sleep(0.5)

        except Exception as e:
            errors += 1
            print(f"ОШИБКА: {type(e).__name__}")
            time.sleep(2)

    print(f"  Итог: сохранено {saved} дней | пустых {empty} | ошибок {errors}")
    return saved


def main():
    print("Инициализирую БД...")
    init_db()

    print("Загружаю токен...")
    token = load_access_token()

    with open(UNITS_FILE, "r", encoding="utf-8") as f:
        units = json.load(f)

    end_date = date.today() - timedelta(days=1)

    # Считаем общий объём работы
    total_chunks = 0
    print("\nПлан загрузки:")
    print(f"{'Пиццерия':<12} {'Старт':>12} {'Дней':>6} {'Запросов':>9}")
    print("-" * 45)
    for unit in units:
        unit_start = get_unit_start_date(unit)
        days = (end_date - unit_start).days + 1
        chunks = (days // 9) + 1
        total_chunks += chunks
        print(f"  {unit['name']:<10} {str(unit_start):>12} {days:>6} {chunks:>9}")

    print("-" * 45)
    print(f"  {'ИТОГО':<10} {'':>12} {'':>6} {total_chunks:>9} запросов")
    print(f"\n  Примерное время: ~{total_chunks // 120 + 1} минут")
    print(f"  (при паузе 0.5 сек между запросами)\n")

    answer = input("Начать загрузку? (да/нет): ").strip().lower()
    if answer not in ("да", "д", "yes", "y"):
        print("Отменено.")
        return

    print("\nНачинаю бэкфилл...\n")
    grand_total = 0

    for unit in units:
        unit_id = unit["id"]
        unit_name = unit["name"]
        unit_start = get_unit_start_date(unit)
        saved = backfill_unit(token, unit_id, unit_name, unit_start, end_date)
        grand_total += saved

    print(f"\n{'='*60}")
    print(f"БЭКФИЛЛ ЗАВЕРШЁН!")
    print(f"Всего загружено: {grand_total} записей")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()