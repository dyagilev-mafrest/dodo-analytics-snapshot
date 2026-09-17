"""Повторы для Google Sheets API.

Зачем: Google периодически отдаёт 503/500 «The service is currently unavailable»
на ровном месте — 03.09.2026 на этом упал Daily Warehouse Load (12 предыдущих
запусков подряд были успешны). Ошибка транзиентная, лечится повтором, но её
некому было повторить: скрипты ходили в gspread напрямую.

Повторяем только то, что имеет смысл повторять: 5xx, 429 (rate limit) и обрывы
сети. 401/403/404 — это права доступа, переименованный лист или отозванный
сервисный аккаунт: повтор их не исправит, а только задержит уведомление.

Использование:

    from sheets_retry import retry_google

    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы")
    rows = retry_google(lambda: sh.worksheet(SHEET).get_all_values(), what=f"лист {SHEET}")
"""
import random
import sys
import time
from typing import Callable, TypeVar

import gspread
import requests

T = TypeVar("T")

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
DEFAULT_ATTEMPTS = 5
BASE_DELAY_SECONDS = 2.0


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, gspread.exceptions.APIError):
        return _status_of(exc) in RETRYABLE_STATUSES
    return False


def _status_of(exc: gspread.exceptions.APIError) -> int | None:
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        return response.status_code
    # у части версий gspread код лежит только в тексте: "APIError: [503]: ..."
    text = str(exc)
    start, end = text.find("["), text.find("]")
    if 0 <= start < end:
        code = text[start + 1 : end]
        return int(code) if code.isdigit() else None
    return None


def retry_google(
    call: Callable[[], T],
    what: str = "запрос к Google Sheets",
    attempts: int = DEFAULT_ATTEMPTS,
) -> T:
    """Выполняет call, повторяя транзиентные ошибки Google с экспоненциальной паузой.

    Паузы 2, 4, 8, 16 секунд плюс джиттер — суммарно до ~30 секунд ожидания, что
    заметно меньше таймаута джоба и достаточно для типичного 503.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — решение принимает _is_retryable
            if attempt == attempts or not _is_retryable(exc):
                raise
            delay = BASE_DELAY_SECONDS * 2 ** (attempt - 1) + random.uniform(0, 1)
            print(
                f"{what}: попытка {attempt} из {attempts} не удалась ({exc}); "
                f"повтор через {delay:.1f} с",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("недостижимо: цикл всегда возвращает результат или пробрасывает ошибку")
