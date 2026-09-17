import time
from datetime import date

from get_revenue import load_access_token, fetch_daily_sales
from db import init_db, save_daily_sales

init_db()
token = load_access_token()

missing = {
    "000d3a240c719a8711e68aba13f953c8": [
        date(2016, 7, 30), date(2016, 7, 31), date(2016, 8, 31),
        date(2016, 11, 21), date(2017, 6, 22), date(2017, 8, 6),
        date(2018, 7, 9), date(2020, 9, 29),
        date(2026, 4, 27), date(2026, 4, 28), date(2026, 4, 29),
        date(2026, 4, 30), date(2026, 5, 1),
    ],
    "b69e16149f1fabb511eec3f6afb944f7": [
        date(2026, 4, 20), date(2026, 4, 21),
    ],
}

# Закрываем общий разрыв с текущей датой для всех пиццерий
recent_gap = [date(2026, 6, 16), date(2026, 6, 17)]
all_units = list(missing.keys()) + [
    "000d3a21da51a81211e964acd9bafbe7",
    "000d3abf84c3bb2e11ec7ce6666a38d4",
    "000d3abf84c3bb2e11ec8faf256493b4",
    "9e5cdb331af4833411ed7b5d9f16042c",
    "11efd95997bf51df4b84427965baea80",
]
for unit_id in all_units:
    missing.setdefault(unit_id, [])
    for d in recent_gap:
        if d not in missing[unit_id]:
            missing[unit_id].append(d)

total_ok = 0
total_empty = 0
total_error = 0

for unit_id, dates in missing.items():
    for d in sorted(dates):
        try:
            data = fetch_daily_sales(token, unit_id, d, d)
            result = data.get("result", [])
            if result:
                save_daily_sales(result)
                print(f"{unit_id[:8]}... {d}: OK")
                total_ok += 1
            else:
                print(f"{unit_id[:8]}... {d}: пусто (нет данных в API)")
                total_empty += 1
        except Exception as e:
            print(f"{unit_id[:8]}... {d}: ОШИБКА {type(e).__name__}")
            total_error += 1
        time.sleep(2)

print()
print(f"Загружено: {total_ok} | Пусто: {total_empty} | Ошибок: {total_error}")