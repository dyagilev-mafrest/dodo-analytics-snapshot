# stop_events.lost_revenue for event_type='product' rows.
#
# Formula (per business rule):
#   lost_revenue (per calendar day the stop overlaps) =
#       (stop_minutes_within_working_hours / working_hours_minutes_that_day)
#       * avg(product revenue in this unit on the same weekday, 3 preceding occurrences,
#             skipping any preceding week where the product was itself in a stop that day)
#   Multi-day stops are split into per-calendar-day segments and summed.
#
# Only event_type='product' is computed: product-stop entity_id is a real productId,
# joinable to product_daily_revenue. Ingredient-stops cascade into product-stops
# covering the same window, so their revenue impact is already captured at the
# product level — computing it again on the ingredient row would double-count.
#
# Only closed stops (ended_at_local IS NOT NULL) are computed; still-open stops
# get recomputed once they close on a later run.
#
# Working hours source: all_stores.json (workingSchedule.stationary), refreshed
# manually via get_stores.py — no live API call needed here.
#
# Batched: all reference data (baseline revenue, stop intervals for the exclusion
# rule) is pulled in a handful of bulk queries and held in memory, so the per-stop
# loop makes zero DB round-trips. The N+1-query version of this script took hours
# on ~23k stops; this version runs in seconds.
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

import psycopg2.extras

from db import get_connection, get_cursor

STORES_FILE = "all_stores.json"


def _minutes(t: str) -> int:
    h, m, *_ = t.split(":")
    return int(h) * 60 + int(m)


def _union_entry(a: dict | None, b: dict | None) -> dict | None:
    """Объединение двух режимов работы за один день: пиццерия продаёт, если
    открыт хоть один канал."""
    live = [x for x in (a, b) if x and not x.get("isClosed")]
    if not live:
        return None
    if any(x.get("isRoundTheClock") for x in live):
        return {"isRoundTheClock": True, "isClosed": False}
    spans = []
    for x in live:
        begin = _minutes(x["beginTime"])
        end = _minutes(x["endTime"])
        if end <= begin:
            end += 24 * 60          # закрытие после полуночи
        spans.append((begin, end))
    begin = min(b0 for b0, _ in spans)
    end = max(e0 for _, e0 in spans)
    # working_window разбирает часы как int, поэтому «25:00:00» корректно даёт
    # закрытие в час ночи следующего дня.
    return {"beginTime": f"{begin // 60}:{begin % 60:02d}:00",
            "endTime": f"{end // 60}:{end % 60:02d}:00",
            "isRoundTheClock": False, "isClosed": False}


def load_schedules(schedule_type: str = "sales") -> dict[str, dict]:
    """unit_id (lowercase) -> {dayOfWeek: entry}.

    schedule_type: "sales" (по умолчанию) — ОБЪЕДИНЕНИЕ зала и доставки;
    "stationary" — только зал; "delivery" — только доставка.

    ⚠️ Для упущенной выручки от стопов нужен именно "sales". Стоп продукта
    режет продажи во всех каналах, поэтому знаменатель — время, когда пиццерия
    продаёт хоть через один канал.

    Проверено на 828 строках (стоп × неделя) их построчной выгрузки, где есть
    поле «Время стопа в рабочие часы» — сравнивали наш расчёт с их значением:

        пиццерия     зал   доставка   объединение
        Якутск-1..4  100%      100%          100%
        Якутск-5      66%      100%          100%
        Якутск-6      65%      100%          100%
        Якутск-7     100%       94%          100%
        ИТОГО         85%       99%          100%

    Ни зал, ни доставка по отдельности не описывают эталон, объединение
    описывает точно. Причины известны от заказчика: доставка Якутск-5 и
    Якутск-6 круглосуточная с ноября 2025, а зал Якутск-6 и Якутск-7 летом
    работал до 24:00 (у остальных до 23:00).

    ⚠️ all_stores.json — ТЕКУЩИЙ снимок расписаний, истории в нём нет. Для
    периодов, когда режим был другим (например, до перехода Якутск-5/6 на
    круглосуточную доставку в ноябре 2025), окно посчитается по сегодняшнему
    режиму. Пока сверяемся на 2026 годе, это не мешает."""
    with open(STORES_FILE, encoding="utf-8") as f:
        stores = json.load(f)
    out = {}
    for s in stores:
        ws = s.get("workingSchedule", {})
        if schedule_type == "sales":
            zal = {e["dayOfWeek"]: e for e in ws.get("stationary", [])}
            dlv = {e["dayOfWeek"]: e for e in ws.get("delivery", [])}
            by_day = {}
            for day in set(zal) | set(dlv):
                merged = _union_entry(zal.get(day), dlv.get(day))
                if merged:
                    by_day[day] = {"dayOfWeek": day, **merged}
        else:
            by_day = {e["dayOfWeek"]: e for e in ws.get(schedule_type, [])}
        out[s["id"].lower()] = by_day
    return out


def working_window(schedule: dict, day: date) -> tuple[datetime, datetime] | None:
    """Returns (open, close) datetimes for `day`, or None if closed that day."""
    entry = schedule.get(day.strftime("%A"))
    if not entry or entry.get("isClosed"):
        return None
    if entry.get("isRoundTheClock"):
        return datetime.combine(day, datetime.min.time()), datetime.combine(day + timedelta(days=1), datetime.min.time())
    begin_h, begin_m, *_ = entry["beginTime"].split(":")
    end_h, end_m, *_ = entry["endTime"].split(":")
    open_dt = datetime.combine(day, datetime.min.time()) + timedelta(hours=int(begin_h), minutes=int(begin_m))
    close_dt = datetime.combine(day, datetime.min.time()) + timedelta(hours=int(end_h), minutes=int(end_m))
    if close_dt <= open_dt:
        close_dt += timedelta(days=1)  # overnight schedule
    return open_dt, close_dt


def overlap_minutes(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max(0.0, (end - start).total_seconds() / 60)


def day_segments(started_at: datetime, ended_at: datetime):
    """Split [started_at, ended_at] into per-calendar-day (date, seg_start, seg_end) pieces."""
    cur = started_at
    while cur.date() <= ended_at.date():
        next_midnight = datetime.combine(cur.date() + timedelta(days=1), datetime.min.time())
        seg_end = min(ended_at, next_midnight)
        yield cur.date(), cur, seg_end
        cur = next_midnight


def merge_intervals(rows: list[tuple]) -> list[dict]:
    """rows: [(id, started_at, ended_at), ...] for ONE (unit_id, entity_id) — the raw
    production/stop-sales-products feed genuinely contains overlapping/nested rows for
    the same product (found 2026-08-28 reconciling against a real Dodo IS export: ~3.5k
    such pairs network-wide, ~80% of them one row's interval fully nested inside
    another's, same "reason", started/ended a few seconds apart — looks like a Dodo IS
    API quirk, not bad data on our side). Computing lost_revenue independently per row
    and summing double-counts the overlapping minutes.
    Returns non-overlapping merged segments, each carrying the ids of every row that
    contributed to it (sorted by start; the first id is the "primary" row that will
    carry the merged segment's lost_revenue, the rest get 0 — see main())."""
    sorted_rows = sorted(rows, key=lambda r: r[1])
    segments: list[dict] = []
    for rid, start, end in sorted_rows:
        if segments and start <= segments[-1]["end"]:
            if end > segments[-1]["end"]:
                segments[-1]["end"] = end
            segments[-1]["ids"].append(rid)
        else:
            segments.append({"start": start, "end": end, "ids": [rid]})
    return segments


def load_long_stop_days(cur, unit_ids: set, product_ids: set, range_start: date, range_end: date):
    """(unit_id, product_id) -> set of dates where the product's total stop duration
    that day exceeded 120 minutes — per the Dodo IS KB formula ("Дашборд Стопы
    продуктов и ингредиентов"): "в расчете средней выручки не учитывается выручка
    за день, в котором продукт был в стопе более 2 часов". A day with a short stop
    (e.g. a nightly 30-minute ingredient shortage) must NOT be excluded — only days
    with cumulative stop time > 2h are. Multi-day stops are apportioned via
    day_segments() so a stop's minutes are attributed to the calendar day they fall in.
    Open stops (ended_at_local IS NULL) are treated as extending through range_end."""
    cur.execute(
        """
        SELECT unit_id, entity_id, started_at_local, ended_at_local
        FROM stop_events
        WHERE event_type = 'product'
          AND unit_id = ANY(%s) AND entity_id = ANY(%s)
          AND started_at_local::date <= %s
          AND (ended_at_local IS NULL OR ended_at_local::date >= %s)
        """,
        (list(unit_ids), list(product_ids), range_end, range_start),
    )
    by_entity = defaultdict(list)
    for row in cur.fetchall():
        ended = row["ended_at_local"] or datetime.combine(range_end + timedelta(days=1), datetime.min.time())
        by_entity[(row["unit_id"], row["entity_id"])].append((None, row["started_at_local"], ended))
    # merge overlapping/nested rows first (same bug as lost_revenue itself — see
    # merge_intervals docstring) so a day's stop-minutes aren't double-counted here too,
    # which would wrongly exclude weeks whose true cumulative downtime was under 2h.
    minutes_by_day = defaultdict(float)
    for (unit_id, product_id), interval_rows in by_entity.items():
        for seg in merge_intervals(interval_rows):
            for day, seg_start, seg_end in day_segments(seg["start"], seg["end"]):
                minutes_by_day[(unit_id, product_id, day)] += (seg_end - seg_start).total_seconds() / 60
    long_stop_days = defaultdict(set)
    for (unit_id, product_id, day), minutes in minutes_by_day.items():
        if minutes > 120:
            long_stop_days[(unit_id, product_id)].add(day)
    return long_stop_days


def load_revenue_map(cur, unit_ids: set, product_ids: set, range_start: date, range_end: date):
    # product_daily_revenue is split per sales_channel (for the pulse-week "Дисконт"
    # metric) — sum across channels here since this baseline only cares about total
    # revenue per (unit_id, product_id, date), not the channel split.
    cur.execute(
        """
        SELECT unit_id, product_id, date, SUM(revenue) AS revenue
        FROM product_daily_revenue
        WHERE unit_id = ANY(%s) AND product_id = ANY(%s)
          AND date BETWEEN %s AND %s
        GROUP BY unit_id, product_id, date
        """,
        (list(unit_ids), list(product_ids), range_start, range_end),
    )
    return {(row["unit_id"], row["product_id"], row["date"]): float(row["revenue"]) for row in cur.fetchall()}


# Сколько предыдущих одинаковых дней недели идёт в базу. РОВНО три, поиск НЕ
# продлевается — и это принципиально.
#
# Раньше здесь стояло 15 недель с продлением поиска: если продукт стоял три
# последних одинаковых дня, искали глубже, чтобы «метрика не обнулилась».
# Построчная выгрузка Dodo IS (09.09.2026) показала, что обнуление — это и есть
# их поведение. «Сыр Блю Чиз», стоп с 10.07, не закрыт:
# 54 842,88 → 33 785,76 → 10 598,88 → 0,00 → 0,00. Через три недели непрерывного
# стопа все три предыдущих дня стоповые, база равна нулю, упущенной выручки нет.
#
# Смысл в этом есть: если позиции нет месяц, потерянных продаж больше нет —
# спрос ушёл к другим позициям или к конкурентам, и приписывать простою
# «недополученную» выручку месячной давности значит выдумывать деньги.
#
# Измерено на сверке за 8 недель, с фильтрами дашборда Dodo IS (классификация
# продукта: Новинка/Обязательный ассортимент; классификация ингредиента:
# категории А/В/С, локальные, сезонные):
#   окно 3, стоп-день = нулевой вклад, делим на 3      87 %  ← так делает дашборд
#   окно 3, стоп-день исключается из среднего         119 %  (так написано в KB)
#   окно 4, стоп-день исключается                     131 %
#   окно 6, стоп-день исключается                     152 %
#   окно 8, стоп-день исключается                     174 %
BASE_WEEKS = 3

# Окно предзагрузки выручки: базе нужно 3 недели до самого раннего дня стопа,
# плюс запас.
MAX_LOOKBACK_WEEKS = BASE_WEEKS + 1


def avg_prior_same_weekday_revenue(long_stop_days: dict, revenue_map: dict, unit_id: str, product_id: str, day: date) -> float:
    """Средняя выручка продукта по трём предыдущим таким же дням недели.

    Выручка дня, в котором продукт был в стопе больше 2 часов, НЕ УЧИТЫВАЕТСЯ,
    но знаменатель остаётся равным трём. То есть стоп-день даёт нулевой вклад,
    а не выпадает из среднего.

    ⚠️ Это расходится с примером в KB-статье «Дашборд "Стопы продуктов и
    ингредиентов"», где написано:

        Три последние пятницы: 7 апреля — 9 000 ₽, 31 марта — 10 000 ₽,
        24 марта — 11 000 ₽. 7 апреля Додстер был в стопе 3 часа, поэтому не
        учитываем этот день. Средняя = (10 000 + 11 000) / 2 = 10 500 ₽.

    То есть в статье делится на ДВА. Но сам дашборд делит на ТРИ — это доказано
    арифметикой на 2359 однодневных стопах из их построчной выгрузки. Мы
    восстановили их базу из формулы (база = упущенная выручка / доля стопа,
    рабочее окно у нас совпадает с их «временем стопа в рабочие часы» с
    медианой 1.000) и сравнили в разрезе «сколько дней выпало»:

        выпало дней  стопов   наше «делим на уцелевшие» / их   наше «/3» / их
        0              1763                            1.00              1.00
        1               446                            1.50              1.00
        2               144                            2.82              0.94

    1.50 = 3/2 и 2.82 ≈ 3/1 — то есть при делении на уцелевшие мы завышаем
    ровно во столько раз, во сколько их знаменатель больше нашего. Сумма базы:
    делением на три — 98 % их, делением на уцелевшие — 113 %.

    Данные важнее текста статьи: считаем на три.

    ⚠️ Не продлевать поиск глубже трёх недель: именно продление (было 15)
    держало метрику вечно ненулевой и давало 254 %.
    """
    stopped = long_stop_days.get((unit_id, product_id), set())
    total = 0.0
    for i in range(1, BASE_WEEKS + 1):
        d = day - timedelta(days=7 * i)
        if d in stopped:
            continue                    # вклад нуля, но знаменатель остаётся 3
        total += revenue_map.get((unit_id, product_id, d), 0.0)
    # Все три дня стоповые -> база нулевая, упущенной выручки нет. Так и должно
    # быть: позиции нет неделями, потерянных продаж больше нет.
    return total / BASE_WEEKS


def compute_days_for_stop(schedules: dict, long_stop_days: dict, revenue_map: dict, unit_id: str, product_id: str,
                          started_at: datetime, ended_at: datetime) -> list[tuple[date, float]]:
    """Упущенная выручка ПО ДНЯМ, которые задел стоп.

    Основной примитив: каждый день считается от своей базы, поэтому у длинного
    стопа суммы по дням убывают. `compute_for_stop` ниже — просто их сумма.
    Подневную раскладку хранит ingredient_stop_impact (миграция 059), чтобы
    дашборд бакетил её сам и не относил всю сумму к одному периоду.
    """
    schedule = schedules.get(unit_id.lower(), {})
    out = []
    for day, seg_start, seg_end in day_segments(started_at, ended_at):
        window = working_window(schedule, day)
        if not window:
            continue
        open_dt, close_dt = window
        working_minutes = (close_dt - open_dt).total_seconds() / 60
        if working_minutes <= 0:
            continue
        stopped_minutes = overlap_minutes(seg_start, seg_end, open_dt, close_dt)
        if stopped_minutes <= 0:
            continue
        avg_revenue = avg_prior_same_weekday_revenue(long_stop_days, revenue_map, unit_id, product_id, day)
        if avg_revenue <= 0:
            continue
        out.append((day, round((stopped_minutes / working_minutes) * avg_revenue, 2)))
    return out


def compute_for_stop(schedules: dict, long_stop_days: dict, revenue_map: dict, unit_id: str, product_id: str,
                      started_at: datetime, ended_at: datetime) -> float:
    """Сумма подневных значений — для stop_events.lost_revenue, где хранится одно
    число на стоп. Подневная раскладка — compute_days_for_stop."""
    days = compute_days_for_stop(schedules, long_stop_days, revenue_map, unit_id, product_id, started_at, ended_at)
    return round(sum(v for _, v in days), 2)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="filters by started_at_local date")
    parser.add_argument("--to-date",   required=True)
    args = parser.parse_args()

    schedules = load_schedules()
    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        SELECT id, unit_id, entity_id, started_at_local, ended_at_local
        FROM stop_events
        WHERE event_type = 'product'
          AND entity_id IS NOT NULL
          AND ended_at_local IS NOT NULL
          AND started_at_local::date BETWEEN %s AND %s
    """, (args.from_date, args.to_date))
    rows = cur.fetchall()
    print(f"Найдено закрытых product-стопов: {len(rows)}")

    if not rows:
        conn.close()
        return

    unit_ids = {row["unit_id"] for row in rows}
    product_ids = {row["entity_id"] for row in rows}
    # baseline lookback needs up to MAX_LOOKBACK_WEEKS before the earliest stop day
    # (avg_prior_same_weekday_revenue searches that far back for chronically-stopped products)
    lookback_start = from_date - timedelta(weeks=MAX_LOOKBACK_WEEKS)

    print("Загружаю дни с длинными (>2ч) стопами и выручку одним запросом каждая...")
    long_stop_days = load_long_stop_days(cur, unit_ids, product_ids, lookback_start, to_date)
    revenue_map = load_revenue_map(cur, unit_ids, product_ids, lookback_start, to_date)
    print(f"  дней с длинным стопом (>2ч): {sum(len(v) for v in long_stop_days.values())}, строк выручки: {len(revenue_map)}")

    by_entity = defaultdict(list)
    for row in rows:
        by_entity[(row["unit_id"], row["entity_id"])].append(
            (row["id"], row["started_at_local"], row["ended_at_local"])
        )

    updates = []
    for (unit_id, product_id), interval_rows in by_entity.items():
        for seg in merge_intervals(interval_rows):
            lost = compute_for_stop(
                schedules, long_stop_days, revenue_map, unit_id, product_id,
                seg["start"], seg["end"],
            )
            # merged segment's lost_revenue goes to the earliest-starting row in the
            # group; every other row that got folded into this segment (overlapping/
            # nested with it) is zeroed so summing stop_events.lost_revenue doesn't
            # double-count the shared downtime.
            updates.append((seg["ids"][0], lost))
            for other_id in seg["ids"][1:]:
                updates.append((other_id, 0.0))

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE stop_events AS se
        SET lost_revenue = v.lost_revenue
        FROM (VALUES %s) AS v(id, lost_revenue)
        WHERE se.id = v.id
        """,
        updates,
        template="(%s, %s)",
        page_size=1000,
    )
    conn.commit()
    conn.close()
    print(f"Обновлено lost_revenue для {len(updates)} стопов.")


if __name__ == "__main__":
    main()
