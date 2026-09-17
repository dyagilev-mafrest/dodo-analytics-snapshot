import os
import json
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

from db import init_db, save_daily_sales, show_daily_sales

load_dotenv()
COUNTRY = os.getenv("DODO_COUNTRY", "ru").lower()
API_BASE = "https://api.dodois.io/dodopizza"
TOKENS_FILE = "tokens.json"
UNITS_FILE = "yakutsk_units.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):
    """Отправляет сообщение в Telegram. Не падает, если что-то не так с уведомлением."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram не настроен, пропускаю уведомление.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        httpx.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10.0,
        )
    except Exception as e:
        print(f"Не удалось отправить уведомление в Telegram: {e}")


def load_access_token():
    """Берём access_token из tokens.json или из секретов (см. load_tokens()).

    ⚠️ Токен НЕ обновляется автоматически — get_access_token() рефрешит только
    при force_refresh=True. Раньше в этом докстринге было написано обратное
    («затем обновляет access_token через refresh_token»), и на это полагались:
    401 никто не ловил, и 02.09.2026 загрузка выручки легла целиком на всех
    7 пиццериях, потому что секрет в запущенном ране был уже просроченным.
    Обновление на 401 живёт в fetch_daily_sales() — как и во всех остальных
    загрузчиках.
    """
    from get_sales import get_access_token

    return get_access_token()


def load_units():
    if not os.path.exists(UNITS_FILE):
        env_units = os.getenv("YAKUTSK_UNITS_JSON")
        if env_units:
            units = json.loads(env_units)
            with open(UNITS_FILE, "w", encoding="utf-8") as f:
                json.dump(units, f, ensure_ascii=False, indent=2)
            return units
        raise FileNotFoundError(
            f"{UNITS_FILE} не найден и переменная окружения YAKUTSK_UNITS_JSON не задана."
        )
    with open(UNITS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_daily_sales(access_token, unit_uuid, from_date, to_date, retries: int = 3):
    """Тот же обработчик 401, что и в остальных загрузчиках (get_delivery.py,
    get_product_sales.py и др.): access_token живёт сутки, а в GitHub Actions
    задание получает значение секрета, снятое на СТАРТЕ рана — token_guard,
    отработавший в том же ране, его уже не поменяет. Единственный надёжный путь —
    поймать 401 и дорефрешить самому.

    Возвращаемое значение — (данные, актуальный access_token): обновлённый токен
    отдаём наверх, чтобы следующая пиццерия не повторяла тот же 401."""
    url = f"{API_BASE}/{COUNTRY}/finances/sales/units/daily"
    params = {
        "fromDate": from_date.isoformat(),
        "toDate": to_date.isoformat(),
        "units": unit_uuid,
    }
    from get_sales import get_access_token

    for attempt in range(retries):
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        response = httpx.get(url, headers=headers, params=params, timeout=60.0)
        if response.status_code == 401 and attempt < retries - 1:
            access_token = get_access_token(force_refresh=True, _stale_token=access_token)
            continue
        if response.status_code != 200:
            print(f"    Статус: {response.status_code}, Тело: {response.text}")
        response.raise_for_status()
        return response.json(), access_token
    raise RuntimeError("Не удалось получить данные: 401 после обновления токена")


def main():
    print("Инициализирую БД...")
    init_db()

    print("Загружаю токен...")
    token = load_access_token()

    print("Загружаю список пиццерий...")
    units = load_units()
    print(f"Найдено пиццерий: {len(units)}")

    # Самовосстановление: грузим последние N дней, а не только вчера.
    # Если какой-то запуск был пропущен, следующий успешный закроет пробел.
    # save_daily_sales идемпотентна (ON CONFLICT DO UPDATE), повторы безопасны.
    DAYS_BACK = 5
    today = date.today()
    target_dates = [today - timedelta(days=n) for n in range(1, DAYS_BACK + 1)]
    from_date = min(target_dates)
    to_date = max(target_dates)
    print(f"\nТяну данные за период {from_date} — {to_date} (последние {DAYS_BACK} дней):\n")

    total_saved = 0
    errors = []

    for unit in units:
        unit_id = unit["id"]
        unit_name = unit.get("name", unit_id[:8])
        print(f"  [{unit_name}] ", end="", flush=True)

        try:
            data, token = fetch_daily_sales(token, unit_id, from_date, to_date)
            result = data.get("result", [])

            if result:
                save_daily_sales(result)
                days_count = len(result)
                period_sales = sum(r.get("sales", 0) for r in result)
                print(f"OK  {days_count} дней | {period_sales:>12,.0f} руб всего")
                total_saved += 1
            else:
                print("нет данных за период")

        except Exception as e:
            print(f"ОШИБКА: {type(e).__name__}")
            errors.append(unit_name)

    print(f"\n{'='*50}")
    print(f"Обработано: {total_saved} из {len(units)} пиццерий")
    if errors:
        print(f"Ошибки: {', '.join(errors)}")

    show_daily_sales()

    if total_saved == 0:
        send_telegram(
            f"❌ Dodo Analytics: загрузка за {from_date}—{to_date} полностью провалилась.\n"
            f"Ошибки: {', '.join(errors)}"
        )
        # Полный отказ (например, невалидный OAuth-токен) не должен маскироваться
        # зелёным статусом джоба — иначе пропуск дней остаётся незамеченным.
        raise SystemExit(1)
    elif errors:
        send_telegram(
            f"⚠️ Dodo Analytics: загрузка за {from_date}—{to_date} завершена частично.\n"
            f"Успешно: {total_saved} из {len(units)}\n"
            f"Ошибки: {', '.join(errors)}"
        )
    else:
        send_telegram(
            f"✅ Dodo Analytics: данные за последние {DAYS_BACK} дней загружены, "
            f"{total_saved} из {len(units)} пиццерий."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(
            f"❌ Dodo Analytics: критическая ошибка загрузки.\n"
            f"{type(e).__name__}: {e}"
        )
        raise