"""Единственное место хранения и обновления OAuth-токена Dodo IS.

Почему не секреты GitHub (как было до 14.09.2026):

1. `refresh_token` у Dodo IS одноразовый. Писателей в секрет было двое — CI и
   локальная машина, — и каждый мог сжечь токен другому. Пять механизмов отказа
   за два месяца, последний положил все 13 загрузчиков на сутки.
2. Задание в GitHub Actions получает значения секретов, снятые на СТАРТЕ прогона.
   Рефреш, сделанный внутри того же прогона, до соседних джоб не доезжает, зато
   отзывает их access_token. Из-за этого стража пришлось выносить из daily_all.

В БД обе проблемы снимаются: строка одна, читается в момент обращения, а любое
обновление идёт под advisory-блокировкой — второй писатель ждёт и получает уже
обновлённую пару вместо того, чтобы сжечь её повторным рефрешем.

Fallback: при USE_CLOUD_DB=false (локальный SQLite) хранилище недоступно, и
вызывающий код остаётся на старом пути через tokens.json — см. get_sales.py.
"""

import os
from datetime import datetime, timedelta, timezone

from db import USE_CLOUD_DB, get_connection

PROVIDER = "dodo_is"

# Ключ advisory-блокировки: произвольное, но фиксированное число. Блокировка
# транзакционная (pg_advisory_xact_lock) — снимается сама на COMMIT/ROLLBACK,
# поэтому упавший процесс не оставляет её висеть.
LOCK_KEY = 8_615_734_001

# За сколько до истечения считаем токен непригодным и рефрешим заранее. Загрузчик
# может идти десятки минут (productivity 430 с, stops 468 с), поэтому запас берём
# с походом на самый долгий из них.
EXPIRY_MARGIN = timedelta(minutes=15)


def available() -> bool:
    """Хранилище работает только на облачной БД."""
    return USE_CLOUD_DB


def _now():
    return datetime.now(timezone.utc)


def _who() -> str:
    """Кто обновляет токен — попадает в updated_by."""
    job = os.getenv("GITHUB_JOB")
    if job:
        return f"ci:{job}"
    return "local"


def _refresh(refresh_token: str) -> dict:
    # Импорт внутри функции: get_sales импортирует этот модуль, а не наоборот.
    from get_sales import refresh_access_token

    return refresh_access_token(refresh_token)


def _store(cur, tokens: dict, expires_at):
    cur.execute(
        """
        INSERT INTO dodo_oauth_token
            (provider, access_token, refresh_token, expires_at, updated_at, updated_by)
        VALUES (%s, %s, %s, %s, now(), %s)
        ON CONFLICT (provider) DO UPDATE SET
            access_token  = EXCLUDED.access_token,
            refresh_token = EXCLUDED.refresh_token,
            expires_at    = EXCLUDED.expires_at,
            updated_at    = EXCLUDED.updated_at,
            updated_by    = EXCLUDED.updated_by
        """,
        (PROVIDER, tokens["access_token"], tokens["refresh_token"], expires_at, _who()),
    )


def _expires_at(tokens: dict):
    return _now() + timedelta(seconds=int(tokens.get("expires_in", 86400)))


def _seed_pair() -> dict:
    """Первая пара для пустой таблицы: из секретов окружения или из tokens.json.

    Нужна ровно один раз — при переходе на хранилище. Дальше строка живёт сама.
    """
    env_access = os.getenv("DODO_ACCESS_TOKEN")
    env_refresh = os.getenv("DODO_REFRESH_TOKEN")
    if env_access and env_refresh:
        return {"access_token": env_access, "refresh_token": env_refresh}

    from get_sales import load_tokens

    tokens = load_tokens()
    if tokens and "refresh_token" in tokens:
        return tokens

    raise RuntimeError(
        "Таблица dodo_oauth_token пуста, и взять первую пару неоткуда: нет ни "
        "DODO_ACCESS_TOKEN/DODO_REFRESH_TOKEN в окружении, ни tokens.json. "
        "Нужна ручная авторизация device flow (локальный запуск get_sales.py)."
    )


def get_access_token(force_refresh: bool = False, stale_token: str = None) -> str:
    """Рабочий access_token. Рефрешит сам, если срок близко или поймали 401.

    `stale_token` — токен, на котором вызывающий получил 401. Если в БД уже лежит
    другой, значит кто-то обновил пару, пока мы ждали блокировку: берём готовое и
    не тратим одноразовый refresh_token впустую.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Блокировка до чтения: иначе двое прочитают одну пару и оба пойдут
        # рефрешить, второй получит 400 по уже сожжённому токену.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))

        cur.execute(
            "SELECT access_token, refresh_token, expires_at FROM dodo_oauth_token WHERE provider = %s",
            (PROVIDER,),
        )
        row = cur.fetchone()

        if row is None:
            tokens = _refresh(_seed_pair()["refresh_token"])
            _store(cur, tokens, _expires_at(tokens))
            conn.commit()
            print("token_store: хранилище засеяно, пара обновлена.")
            return tokens["access_token"]

        access_token, refresh_token, expires_at = row

        if force_refresh and stale_token and access_token != stale_token:
            conn.commit()  # снимаем блокировку
            return access_token

        fresh_enough = expires_at > _now() + EXPIRY_MARGIN
        if not force_refresh and fresh_enough:
            conn.commit()
            return access_token

        tokens = _refresh(refresh_token)
        _store(cur, tokens, _expires_at(tokens))
        conn.commit()
        return tokens["access_token"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def peek() -> dict:
    """Состояние строки без рефреша — для диагностики и стража."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT expires_at, updated_at, updated_by FROM dodo_oauth_token WHERE provider = %s",
            (PROVIDER,),
        )
        row = cur.fetchone()
        if row is None:
            return {}
        return {"expires_at": row[0], "updated_at": row[1], "updated_by": row[2]}
    finally:
        conn.close()
