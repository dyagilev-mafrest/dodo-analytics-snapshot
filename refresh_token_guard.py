"""Страж: раз в сутки заранее обновляет OAuth-токен Dodo IS.

С 14.09.2026 источник правды — таблица dodo_oauth_token (см. token_store.py), а
не секреты GitHub. Страж после этого перестал быть единственным, кто умеет
рефрешить: загрузчик и сам обновит пару, если срок близко или поймал 401, и
сделает это под той же блокировкой. Страж остался по двум причинам — обновлять
в тихое время, до цепочки, и падать на сутки раньше загрузчиков, давая окно на
починку.

17.09.2026 отсюда убрано зеркалирование пары в секреты GitHub — переходная
подстраховка на случай потери строки в БД. Писала она по GH_PAT, тот протух
ровно через 90 дней после выпуска (прогон 35184470069) и уронил стража ложной
тревогой: пара в хранилище была обновлена, упало только зеркало. Восстанавливать
PAT не стали — как «засев» секреты всё равно мертвы: рефреш проворачивает
одноразовый refresh_token, а в секрете остаётся предыдущий, уже сожжённый.
Если строку в dodo_oauth_token потеряем, засев делается заново через device flow
(локальный запуск get_sales.py).
"""

import httpx

import token_store

REASON_FILE = "guard_failure_reason.txt"

BURNED_TOKEN_HINT = (
    "refresh_token уже сожжён (Dodo IS проворачивает его при каждом рефреше, он "
    "одноразовый). С переходом на dodo_oauth_token писатель один и блокировка "
    "общая, так что само по себе это случиться не должно: ищи рефреш, сделанный "
    "мимо хранилища — ручную авторизацию device flow или запуск со старым кодом. "
    "Починка: device flow заново (локальный запуск get_sales.py), он запишет "
    "свежую пару в хранилище."
)


def write_reason(text):
    with open(REASON_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    if not token_store.available():
        write_reason(
            "USE_CLOUD_DB выключен — хранилище токена недоступно, стражу не с чем "
            "работать. Проверь секрет USE_CLOUD_DB в репозитории."
        )
        raise RuntimeError("USE_CLOUD_DB=false: хранилище токена недоступно.")

    before = token_store.peek()
    if before:
        print(f"Страж: текущая пара истекает {before['expires_at']}, "
              f"обновлял {before['updated_by']} в {before['updated_at']}.")
    else:
        print("Страж: хранилище пусто — засеваю из окружения или tokens.json.")

    try:
        token_store.get_access_token(force_refresh=True)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400:
            print("Страж: 400 от auth.dodois.io — " + BURNED_TOKEN_HINT)
            write_reason(BURNED_TOKEN_HINT)
        else:
            write_reason(
                f"auth.dodois.io ответил {exc.response.status_code} на рефреш — "
                "это не сожжённый токен, смотри тело ответа в логе прогона."
            )
        raise
    except Exception as exc:
        write_reason(f"{type(exc).__name__}: {exc}")
        raise

    after = token_store.peek()
    print(f"Страж: пара обновлена, действует до {after['expires_at']}.")


if __name__ == "__main__":
    main()
