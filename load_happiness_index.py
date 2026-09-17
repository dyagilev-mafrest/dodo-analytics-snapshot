# Google Sheets -> PostgreSQL (Supabase): happiness_index_monthly
#
# Лист «ИС весь» таблицы «HR метрики Додо Якутск». Индекс счастья по пиццериям
# помесячно плюс заполняемость опроса — та самая доля людей, которые ответили.
#
# РАЗМЕТКА ЛИСТА. Год — отдельный блок из трёх частей: строка с названиями
# месяцев, под ней строка подписей «ИС | % | ИС | % ...», дальше строки
# пиццерий и «Среднее» в конце. У каждого месяца ДВЕ колонки: индекс и
# заполняемость. Год стоит в первой колонке строки месяцев, но у самого старого
# блока (2023) его там нет — он внутри названия месяца («January/Январь 2023»),
# поэтому ищем и там.
#
# Блоки находим по содержимому, а не по номерам строк: между ними попадаются
# осиротевшие строки подписей, а сами блоки двигают при каждой правке.
#
# «Среднее» кладём как unit_name='Сеть' — так же, как в team_metrics_*.
#
# Usage:
#   python load_happiness_index.py            # прочитать и загрузить
#   python load_happiness_index.py --dry-run  # показать и не писать
import json
import os
import re
import sys
from datetime import date

import gspread
from dotenv import load_dotenv

from db import get_connection, get_cursor
from sheets_retry import retry_google

load_dotenv()
SPREADSHEET_ID = "1VxE2k8bSrPJO-lnnkxiqaL418IYkYbhSHXxB-WOQecQ"
SHEET_NAME = "ИС весь"

MONTHS_RU = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}
AVERAGE_LABEL = "среднее"
NETWORK_UNIT = "Сеть"
UNIT_ROW_RE = re.compile(r"^(Якутск-\d+|ПРЦ)$")


def parse_pct(raw: str) -> float | None:
    """«60%» -> 60.0. Прочерк и пустая клетка — нет замера, а не ноль."""
    s = (raw or "").replace("\xa0", "").replace(" ", "").replace("%", "").replace(",", ".").strip()
    if not s or s in {"-", "—", "х", "x"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def month_in(cell: str) -> int | None:
    text = (cell or "").strip().lower()
    for name, number in MONTHS_RU.items():
        if name in text:
            return number
    return None


def year_in(cells: list[str]) -> int | None:
    for cell in cells:
        m = re.search(r"\b(20\d{2})\b", cell or "")
        if m:
            return int(m.group(1))
    return None


def read_rows() -> list[dict]:
    gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы HR-метрик")
    vals = retry_google(
        lambda: sh.worksheet(SHEET_NAME).get_all_values(),
        what=f"чтение листа «{SHEET_NAME}»",
    )

    out: list[dict] = []
    for i, row in enumerate(vals):
        cells = [(c or "").strip() for c in row]
        # Строка месяцев: их в ней должно быть много. Одинокая подпись «ИС | %»
        # или строка пиццерии так не выглядят.
        month_cols = {c: m for c, cell in enumerate(cells) if (m := month_in(cell)) is not None}
        if len(month_cols) < 6:
            continue
        year = year_in(cells)
        if year is None:
            raise ValueError(
                f"В строке {i + 1} листа «{SHEET_NAME}» есть месяцы, но не нашёлся год — "
                f"разметку поменяли, загрузчик надо править."
            )

        # Строки блока: до следующей строки месяцев или до конца листа.
        for j in range(i + 1, len(vals)):
            block = [(c or "").strip() for c in vals[j]]
            if len({c for c, cell in enumerate(block) if month_in(cell) is not None}) >= 6:
                break
            label = block[0] if block else ""
            is_average = label.lower() == AVERAGE_LABEL
            if not is_average and not UNIT_ROW_RE.match(label):
                continue
            unit = NETWORK_UNIT if is_average else label
            for col, month_no in month_cols.items():
                index_pct = parse_pct(block[col]) if col < len(block) else None
                # Заполняемость — соседняя колонка справа, всегда парой.
                rate_pct = parse_pct(block[col + 1]) if col + 1 < len(block) else None
                if index_pct is None and rate_pct is None:
                    continue
                out.append({
                    "month": date(year, month_no, 1), "unit_name": unit,
                    "index_pct": index_pct, "response_rate_pct": rate_pct,
                })

    if not out:
        raise ValueError(f"На листе «{SHEET_NAME}» не разобрано ни одной строки.")
    return out


def upsert(rows: list[dict]):
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO happiness_index_monthly (month, unit_name, index_pct, response_rate_pct)
        VALUES %s
        ON CONFLICT (month, unit_name) DO UPDATE
           SET index_pct = EXCLUDED.index_pct,
               response_rate_pct = EXCLUDED.response_rate_pct,
               loaded_at = now()
    """, [(r["month"], r["unit_name"], r["index_pct"], r["response_rate_pct"]) for r in rows],
        page_size=500)
    conn.commit()
    conn.close()


def main():
    rows = read_rows()
    months = sorted({r["month"] for r in rows})
    print(f"{len(rows)} строк: {months[0]} - {months[-1]}")
    print("месяц       ИС    заполняемость")
    for r in sorted((r for r in rows if r["unit_name"] == NETWORK_UNIT), key=lambda r: r["month"])[-12:]:
        ix = "-" if r["index_pct"] is None else f"{r['index_pct']:.0f}%"
        rt = "-" if r["response_rate_pct"] is None else f"{r['response_rate_pct']:.0f}%"
        print(f"  {r['month']}  {ix:>5}  {rt:>14}")

    if "--dry-run" in sys.argv:
        print("\n--dry-run: в БД не пишем.")
        return
    upsert(rows)
    print(f"\nЗагружено {len(rows)} строк в happiness_index_monthly.")


if __name__ == "__main__":
    main()
