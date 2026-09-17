import os
import json
import time
import threading

import httpx
from dotenv import load_dotenv

# --- 1. Загружаем секреты из .env ---
load_dotenv()
CLIENT_ID = os.getenv("DODO_CLIENT_ID")
CLIENT_SECRET = os.getenv("DODO_CLIENT_SECRET")
COUNTRY = os.getenv("DODO_COUNTRY", "RU")

TOKEN_URL = "https://auth.dodois.io/connect/token"
DEVICE_URL = "https://auth.dodois.io/connect/deviceauthorization"
# `orders` — /orders/clients-statistics (новые/старые клиенты по юниту за период).
# `user.role:read` — /auth/roles/*, диагностика ролей интеграции.
# Добавлены 15.09.2026 при выяснении, почему в /accounting/sales нет clientId.
# Ответ: дело не в scope и не в ролях 2/7, а в роли 55 «PD view via Client Profile»,
# которую не выдают (персданные клиентов). Вопрос закрыт, clientId у нас не будет.
#
# Строка влияет ТОЛЬКО на device flow (первичная авторизация). refresh_access_token
# scope не передаёт, поэтому CI, обновляя токен, сохраняет уже выданный набор прав.
SCOPE = "shared accounting deliverystatistics productionefficiency stopsales staffshifts:read sales orders incentives user.role:read offline_access"
TOKENS_FILE = "tokens.json"


# --- 2. Сохранение/загрузка токенов ---
def save_tokens(tokens):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f)


def load_tokens():
    if not os.path.exists(TOKENS_FILE):
        # Файла нет на диске (типичная ситуация для GitHub Actions —
        # свежая виртуальная машина без локальной истории).
        # Проверяем, не переданы ли токены через переменные окружения.
        env_access = os.getenv("DODO_ACCESS_TOKEN")
        env_refresh = os.getenv("DODO_REFRESH_TOKEN")
        if env_access and env_refresh:
            tokens = {
                "access_token": env_access,
                "refresh_token": env_refresh,
                "expires_in": 86400,
                "token_type": "Bearer",
                "scope": "shared accounting offline_access",
            }
            save_tokens(tokens)
            return tokens
        return None
    with open(TOKENS_FILE, "r") as f:
        return json.load(f)


# --- 3. Device Flow: первая авторизация через браузер ---
def device_flow_login():
    # Отладочная инфа — поможет, если что-то опять пойдёт не так
    print("=" * 60)
    print("ОТЛАДКА:")
    print(f"  CLIENT_ID = '{CLIENT_ID}' (длина: {len(CLIENT_ID) if CLIENT_ID else 0})")
    print(f"  CLIENT_SECRET длина = {len(CLIENT_SECRET) if CLIENT_SECRET else 0}")
    print(f"  SCOPE = '{SCOPE}'")
    print(f"  URL = {DEVICE_URL}")
    print("=" * 60)

    print("Запрашиваю код устройства...")
    response = httpx.post(
        DEVICE_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": SCOPE,
        },
    )
    print("Статус ответа:", response.status_code)
    print("Тело ответа:", response.text)
    response.raise_for_status()

    # Парсим JSON-ответ в словарь
    device_data = response.json()

    print("\n" + "=" * 60)
    print("ОТКРОЙТЕ В БРАУЗЕРЕ:", device_data["verification_uri_complete"])
    print("ЛИБО ЗАЙДИТЕ НА:", device_data["verification_uri"])
    print("ВВЕДИТЕ КОД:", device_data["user_code"])
    print("=" * 60 + "\n")
    print("Ожидаю подтверждения в браузере...")

    # Опрашиваем сервер каждые N секунд, пока не разрешите доступ
    interval = device_data.get("interval", 5)
    device_code = device_data["device_code"]

    while True:
        time.sleep(interval)
        token_response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        data = token_response.json()

        if token_response.status_code == 200:
            print("Авторизация успешна!")
            save_tokens(data)
            return data

        error = data.get("error")
        if error == "authorization_pending":
            print("Жду... (ещё не подтвердили)")
            continue
        elif error == "slow_down":
            interval += 5
            continue
        else:
            raise Exception(f"Ошибка авторизации: {data}")


# --- 4. Обновление access_token по refresh_token ---
def refresh_access_token(refresh_token):
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    response.raise_for_status()
    new_tokens = response.json()
    save_tokens(new_tokens)
    return new_tokens


_token_lock = threading.Lock()

# --- 5. Главная функция: получаем рабочий access_token ---
def get_access_token(force_refresh: bool = False, _stale_token: str = None):
    """Рабочий access_token для загрузчиков.

    Источник правды — таблица dodo_oauth_token (token_store.py): одна строка, одно
    место рефреша, блокировка. Путь ниже — наследие эпохи секретов GitHub; он
    остаётся живым только при USE_CLOUD_DB=false, когда хранилища нет, и держит
    пару в одном лишь tokens.json: 17.09.2026 запись в секреты GitHub убрана
    отсюда и из стража (подробности — в докстринге refresh_token_guard.py).
    """
    import token_store

    if token_store.available():
        with _token_lock:
            return token_store.get_access_token(force_refresh, _stale_token)

    with _token_lock:
        tokens = load_tokens()

        if tokens is None or "refresh_token" not in tokens:
            tokens = device_flow_login()
        elif "access_token" not in tokens:
            tokens = refresh_access_token(tokens["refresh_token"])
        elif force_refresh:
            # If another thread already refreshed (token differs from the one
            # that triggered the 401), reuse it without another round-trip.
            if _stale_token and tokens.get("access_token") != _stale_token:
                pass
            else:
                print("Обновляю токен...")
                tokens = refresh_access_token(tokens["refresh_token"])

        return tokens["access_token"]


# --- 6. Запуск ---
def main():
    print("Получаю токен...")
    token = get_access_token()
    print(f"Токен получен, длина: {len(token)} символов")
    print("Готово! Можно делать запросы к API.")


if __name__ == "__main__":
    main()