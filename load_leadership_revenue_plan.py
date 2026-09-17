# Google Sheets -> PostgreSQL (Supabase): leadership_revenue_plan
#
# Официальный план выручки по пиццериям на год — таблица «План выручки на 2026 -
# Шах», лист «Финмодель 2026». Это тот план, по которому движется руководство, и
# первая строка месячного отчёта («Выполнение плана по выручке») считается от
# него.
#
# ЧТО БЫЛО ДО 15.09.2026 И ПОЧЕМУ ЭТО ВАЖНО
#
# Прежняя версия не читала таблицу вовсе: числа были вписаны в Python руками,
# снимком от 30.07.2026. И сняты они были НЕ С ТОГО ЛИСТА — с «Финмодель 2025
# план/факт». То есть в базе лежал план 2025 года, подписанный как 2026:
# Якутск-1 январь 16 260 000 вместо 13 538 218, по сети 111 470 000 вместо
# 140 264 870. При фактической выручке января 2026 в 143 108 037 отчёт показал
# бы 128% выполнения плана вместо 102%.
#
# Отсюда два правила ниже, и оба не формальность:
#   * лист выбирается по имени с годом, а год сверяется с заголовком колонок
#     («январь 26»). Перепутать листы молча больше нельзя;
#   * сумма ВСЕХ пиццерий листа сверяется со строкой «1. Выручка» — если
#     разошлись, загрузка падает. Разметка ручная, строки двигают.
#
# Почему «всех», а не «наших семи». В плане с июля 2026 заложен переезд
# Якутска-5 в ТЦ БУМ: Якутску-5 плана назначено 0, а появляются «Якутск 8
# (202 мкрн)» и «Якутск 9 (ТЦ БУМ)». Переезд не состоялся, пиццерия не
# открылась, Якутск-5 работает и делает 21-23 млн в месяц. Если сверять только
# по семи действующим, проверка падала бы на этом каждый месяц — и перестала бы
# ловить то, ради чего заведена: сдвинутые строки и перепутанный лист.
#
# ДВЕ ТАБЛИЦЫ, И ОНИ НЕ ДОЛЖНЫ СХОДИТЬСЯ
#
# Решение руководства 16.09.2026: план считается по строке «1. Выручка» целиком.
# Он утверждён в первом квартале 2026 и с тех пор не корректировался — разбивка
# внутри него уже не отражает сеть, но обещание совета дано по итоговой цифре.
# Отсюда:
#   * leadership_revenue_plan          — по действующим пиццериям. Читает
#     apply_leadership_revenue_plan.py, пересчитывая суточный прогноз внутри
#     месяца; двум неоткрытым точкам там пересчитывать нечего, да и unit_id у
#     них нет.
#   * leadership_revenue_plan_network  — строка «1. Выручка». Читает месячный
#     отчёт («Выполнение плана по выручке»).
# С июля 2026 сумма первой МЕНЬШЕ второй на план неоткрытых точек (за август
# 126 455 028 против 159 467 552). Это не рассинхрон, а зафиксированное
# состояние финмодели — сводить их обратно не надо.
#
# Usage:
#   python load_leadership_revenue_plan.py            # прочитать и загрузить
#   python load_leadership_revenue_plan.py --dry-run  # показать и не писать
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
SPREADSHEET_ID = "1i1ZPUzR9EhqbV60iJ7wdDfdcgLIm4DVo_tjA87JToec"

# Год и лист меняются раз в год вместе с новой финмоделью. Держим их парой:
# YEAR проверяется по заголовку колонок листа, так что рассинхрон вылезет сразу.
YEAR = 2026
SHEET_NAME = f"Финмодель {YEAR}"

# Строка сетевого итога — по ней сверяется сумма пиццерий.
TOTAL_LABEL = "1. Выручка"

UNIT_IDS = {
    "Якутск 1": "000d3a240c719a8711e68aba13f953c8",
    "Якутск 2": "000d3a21da51a81211e964acd9bafbe7",
    "Якутск 3": "000d3abf84c3bb2e11ec7ce6666a38d4",
    "Якутск 4": "000d3abf84c3bb2e11ec8faf256493b4",
    "Якутск 5": "9e5cdb331af4833411ed7b5d9f16042c",
    "Якутск 6": "b69e16149f1fabb511eec3f6afb944f7",
    "Якутск 7": "11efd95997bf51df4b84427965baea80",
}

UNIT_ROW = re.compile(r"^Якутск\s*\d+")

MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

# Сумма семи пиццерий не сходится с сетевым итогом до копейки: в листе показаны
# округлённые до рубля значения. Рубль на строку — честная граница.
TOLERANCE = len(UNIT_IDS) + 1


def parse_rub(raw: str) -> float | None:
    s = raw or ""
    for ch in ("\xa0", " ", " ", " "):
        s = s.replace(ch, "")
    s = s.replace("−", "-").replace(",", ".").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def find_month_columns(vals: list[list[str]]) -> tuple[int, dict[int, int]]:
    """Строка заголовка месяцев и карта «номер месяца -> колонка».

    Ищем по содержимому, а не по номеру строки: над таблицей живут итоговые
    строки финмодели, их число меняется от правки к правке.
    """
    for row_idx, row in enumerate(vals[:40]):
        cols: dict[int, int] = {}
        for col_idx, cell in enumerate(row):
            text = (cell or "").strip().lower()
            for month_no, name in enumerate(MONTHS, start=1):
                if text.startswith(name):
                    # Год в заголовке — две последние цифры: «январь 26».
                    suffix = re.search(r"(\d{2})\s*$", text)
                    if suffix and 2000 + int(suffix.group(1)) != YEAR:
                        raise ValueError(
                            f"Заголовок {cell!r} на листе «{SHEET_NAME}» относится к "
                            f"20{suffix.group(1)} году, а загрузчик настроен на {YEAR}. "
                            f"Ровно так в базу однажды попал план 2025 года под видом 2026."
                        )
                    cols[month_no] = col_idx
        if len(cols) >= 12:
            return row_idx, cols
    raise ValueError(
        f"На листе «{SHEET_NAME}» не нашлась строка с названиями двенадцати месяцев — "
        f"разметку поменяли, загрузчик надо править."
    )


def read_plan() -> tuple[list[dict], dict[int, float], dict[int, float], dict[str, dict[int, float]]]:
    """План по пиццериям и сетевой итог по месяцам."""
    gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    sh = retry_google(lambda: gc.open_by_key(SPREADSHEET_ID), what="открытие таблицы")
    vals = retry_google(
        lambda: sh.worksheet(SHEET_NAME).get_all_values(),
        what=f"чтение листа «{SHEET_NAME}»",
    )

    _, month_cols = find_month_columns(vals)

    rows: list[dict] = []
    totals: dict[int, float] = {}       # строка «1. Выручка» листа
    sheet_total: dict[int, float] = {}  # сумма всех заведений листа
    other: dict[str, dict[int, float]] = {}  # заведения не из UNIT_IDS
    seen: set[str] = set()

    for row in vals:
        label = (row[1] or "").strip() if len(row) > 1 else ""
        if not label:
            continue

        def value(month_no: int) -> float | None:
            col = month_cols[month_no]
            return parse_rub(row[col]) if col < len(row) else None

        if label == TOTAL_LABEL and not totals:
            for month_no in month_cols:
                v = value(month_no)
                if v:
                    totals[month_no] = v
            continue

        if not UNIT_ROW.match(label) or label in seen:
            continue
        seen.add(label)

        unit_id = UNIT_IDS.get(label)
        for month_no in sorted(month_cols):
            v = value(month_no)
            if v is None:
                continue  # пустая клетка — плана нет; ноль это не то же самое
            # В сверку идут все заведения листа, в базу — только наши семь.
            sheet_total.setdefault(month_no, 0.0)
            sheet_total[month_no] += v
            if unit_id is None:
                other.setdefault(label, {})[month_no] = v
                continue
            rows.append({
                "unit_id": unit_id,
                "unit_name": label,
                "month": date(YEAR, month_no, 1),
                "plan_revenue": v,
            })

    missing = set(UNIT_IDS) - seen
    if missing:
        raise ValueError(
            f"На листе «{SHEET_NAME}» не найдены строки пиццерий: {', '.join(sorted(missing))}. "
            f"Молча загружать неполный план нельзя — отчёт покажет выполнение по части сети."
        )
    return rows, totals, sheet_total, other


def verify(sheet_total: dict[int, float], totals: dict[int, float]) -> list[str]:
    """Сумма ВСЕХ заведений листа против сетевого итога — по каждому месяцу.

    Проверяется разбор, а не бизнес: расхождение здесь означает, что строки
    съехали или лист не тот, а не что план странный.
    """
    problems = []
    for month_no, total in sorted(totals.items()):
        got = sheet_total.get(month_no, 0)
        if abs(got - total) > TOLERANCE:
            problems.append(
                f"{YEAR}-{month_no:02d}: сумма заведений листа {got:,.0f}, "
                f"а строка «{TOTAL_LABEL}» {total:,.0f} (расхождение {got - total:,.0f} руб.)"
            )
    return problems


def upsert(rows: list[dict]):
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO leadership_revenue_plan (unit_id, month, plan_revenue)
        VALUES %s
        ON CONFLICT (unit_id, month) DO UPDATE SET plan_revenue = EXCLUDED.plan_revenue
    """, [(r["unit_id"], r["month"], r["plan_revenue"]) for r in rows], page_size=200)
    conn.commit()
    conn.close()


def upsert_network(totals: dict[int, float]):
    """Строка «1. Выручка» — сетевое обещание, по которому судит месячный отчёт."""
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO leadership_revenue_plan_network (month, plan_revenue)
        VALUES %s
        ON CONFLICT (month) DO UPDATE
           SET plan_revenue = EXCLUDED.plan_revenue, loaded_at = now()
    """, [(date(YEAR, m, 1), v) for m, v in sorted(totals.items())], page_size=200)
    conn.commit()
    conn.close()


def main():
    dry_run = "--dry-run" in sys.argv
    rows, totals, sheet_total, other = read_plan()

    print(f"Лист «{SHEET_NAME}», год {YEAR}: {len(rows)} строк по {len(UNIT_IDS)} пиццериям")
    by_month: dict[int, float] = {}
    for r in rows:
        by_month[r["month"].month] = by_month.get(r["month"].month, 0) + r["plan_revenue"]
    for month_no in sorted(by_month):
        total = totals.get(month_no)
        mark = "" if total is None else f" (итог листа {total:>15,.0f})"
        print(f"  {YEAR}-{month_no:02d}  сумма по сети {by_month[month_no]:>15,.0f}{mark}")

    problems = verify(sheet_total, totals)
    if problems:
        print("\nПлан не сходится — загрузка остановлена:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("\nСумма заведений листа сходится с сетевым итогом — разбор верен.")

    if other:
        print("\nВ плане есть, в базу НЕ грузим (заведение не открыто):")
        for name, months in sorted(other.items()):
            filled = {m: v for m, v in months.items() if v}
            if not filled:
                print(f"  {name}: план нулевой во всех месяцах")
                continue
            print(f"  {name}: месяцы {min(filled):02d}-{max(filled):02d}, "
                  f"всего {sum(filled.values()):,.0f} руб.")
        print("  Это заложенный переезд Якутска-5 в ТЦ БУМ, который не состоялся:")
        print("  Якутску-5 плана с июля назначено 0, а он работает и делает 21-23 млн/мес.")
        print("  Выполнение плана отчёт всё равно считает от строки «1. Выручка»:")
        print("  план утверждён в 1 квартале 2026 и не корректировался (решение 16.09.2026).")

    if dry_run:
        print("--dry-run: в БД не пишем.")
        return
    upsert(rows)
    print(f"Загружено {len(rows)} строк в leadership_revenue_plan.")
    upsert_network(totals)
    print(f"Загружено {len(totals)} месяцев в leadership_revenue_plan_network.")


if __name__ == "__main__":
    main()
