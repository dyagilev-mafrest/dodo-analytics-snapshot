# stop_events.lost_revenue for event_type IN ('channel', 'sector') — "Пиццерия" and
# "Сектор" stops in the Dodo IS "Стопы пиццерий и активность секторов доставки" report.
#
# Методика — KB-статья Dodo IS «Дашборд "Стопы пиццерий и активность секторов
# доставки"», id f217fc23-5c56-444b-9482-9983d6bc3636 (раздел «Стандарты управления и
# внедрения в Евразии»), редакция от 23.07.2024 (переход на почасовую гранулярность).
# Построчный разбор совпадений и расхождений — docs/unit-sector-stops-methodology.md.
#
# Formula:
#   упущенная_выручка = сумма по каждому часу, пересекающему стоп (в рабочие часы):
#       (минуты_стопа_в_этом_часу / 60) × средняя_выручка_в_тот_же_час_в_тот_же_день_недели
#                                          за 4 предыдущих недели
#   - неделя пропускается из среднего, если в этот день не было данных о выручке
#   - неделя пропускается, если в этот день у сектора/подсектора был стоп дольше 2 часов
#     (KB формулирует это правило ТОЛЬКО для сектора и подсектора — см.
#     Params.long_stop_rule_channel)
#   - если данных нет ни за одну из 4 недель — фолбэк 5% × средняя выручка ДОСТАВКИ
#     ВСЕЙ ПИЦЦЕРИИ (не самой сущности!) в тот же час/день недели за те же 4 недели.
#     Дословно из методички: "Если не было выручки за все 4 дня, то выручка считается
#     как 5%, умноженное на среднюю выручку доставки за час (за 4 таких же часа и дня
#     недели)". Это верно и для "Пиццерии", и для "Сектора" — в обоих случаях база
#     фолбэка одна и та же (hourly_revenue_by_channel, sales_channel='Delivery'),
#     просто для channel-стопов она же используется и как основная база.
#   - sector-стопы удалённых секторов (см. delivery_sectors.is_deleted, из
#     delivery/delivery-sectors) полностью исключаются — lost_revenue не считается
#     (NULL). Подтверждено документацией Dodo IS: удалённый/перерисованный сектор
#     "продолжает висеть" в сыром фиде delivery/stop-sales-sectors бесконечно и
#     искажает статистику, если его не отфильтровать по актуальному списку секторов.
#   ⚠️ Важная деталь методологии Dodo IS (не наше допущение): упущенная выручка
#   считается ОТ ВЫРУЧКИ ДОСТАВКИ независимо от того, какой канал/причина стопа —
#   "потому что при анализе причин стопов 95% — это нехватка сотрудников". Поэтому
#   для channel-стопов (event_type='channel', entity_name='Delivery'/'Dine-in'/
#   'Takeaway') база всегда берётся из hourly_revenue_by_channel.sales_channel='Delivery',
#   а не из выручки того канала, который реально стоял.
#
# sector-стопы (entity_name = имя сектора) джойнятся с hourly_revenue_by_sector по
# (unit_id, sector_name) — delivery/stop-sales-sectors не отдаёт sectorId, только имя.
#
# Все отклонения от «как сейчас» собраны в Params — их перебирает
# analyze_unit_sector_stops_calibration.py, сравнивая с эталоном Dodo IS. Менять
# дефолты здесь стоит только после прогона этого сравнения.
#
# Батчево: вся справочная выручка и интервалы стопов грузятся несколькими bulk-запросами
# и держатся в памяти — как и compute_lost_revenue.py, без N+1 по стопам.
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from collections import defaultdict

import psycopg2.extras

from db import get_connection, get_cursor
from compute_lost_revenue import load_schedules, working_window


@dataclass(frozen=True)
class Params:
    """Дефолты = методика KB как мы её читаем. Каждое поле — точка расхождения,
    которую перебирает analyze_unit_sector_stops_calibration.py."""

    lookback_weeks: int = 4
    exclude_stop_minutes: int = 120
    fallback_share: float = 0.05
    # KB задаёт правило "день со стопом > 2ч не идёт в среднее" только для сектора и
    # подсектора. True — распространить его и на базу стопов пиццерии (и на базу
    # фолбэка, которая тоже delivery-канальная).
    long_stop_rule_channel: bool = False
    # Обрезать стоп рабочим окном доставки. В KB этого нет, но вне рабочих часов
    # средняя выручка всё равно нулевая (замер: 0.2ч из 231ч по пиццериям).
    clip_working_hours: bool = True
    # entity_category='Complete' vs 'Redirection' (перенаправление заказов — не
    # реальная потеря спроса). Нашего происхождения, в KB не описано.
    channel_complete_only: bool = True
    exclude_deleted_sectors: bool = True


DEFAULT_PARAMS = Params()


def hour_segments(started_at: datetime, ended_at: datetime, schedule: dict, params: Params = DEFAULT_PARAMS):
    """Yields (day, hour, seg_start, seg_end) for each hour-of-day the stop overlaps,
    already clipped to that day's working window."""
    cur = started_at
    while cur < ended_at:
        day = cur.date()
        next_hour = cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        seg_end = min(ended_at, next_hour)
        if not params.clip_working_hours:
            yield day, cur.hour, cur, seg_end
            cur = seg_end
            continue
        window = working_window(schedule, day)
        if window:
            open_dt, close_dt = window
            ov_start = max(cur, open_dt)
            ov_end = min(seg_end, close_dt)
            if ov_end > ov_start:
                yield day, cur.hour, ov_start, ov_end
        cur = seg_end


def load_channel_revenue(cur, unit_ids: set, range_start: date, range_end: date) -> dict:
    cur.execute(
        """
        SELECT unit_id, date, hour, revenue
        FROM hourly_revenue_by_channel
        WHERE unit_id = ANY(%s) AND sales_channel = 'Delivery'
          AND date BETWEEN %s AND %s
        """,
        (list(unit_ids), range_start, range_end),
    )
    return {(row["unit_id"], row["date"], row["hour"]): float(row["revenue"]) for row in cur.fetchall()}


def load_sector_revenue(cur, unit_ids: set, sector_names: set, range_start: date, range_end: date) -> dict:
    cur.execute(
        """
        SELECT unit_id, sector_name, date, hour, SUM(revenue) AS revenue
        FROM hourly_revenue_by_sector
        WHERE unit_id = ANY(%s) AND sector_name = ANY(%s)
          AND date BETWEEN %s AND %s
        GROUP BY unit_id, sector_name, date, hour
        """,
        (list(unit_ids), list(sector_names), range_start, range_end),
    )
    return {(row["unit_id"], row["sector_name"], row["date"], row["hour"]): float(row["revenue"]) for row in cur.fetchall()}


def load_deleted_sector_names(cur, unit_ids: set) -> set:
    """(unit_id, sector_name) pairs where EVERY delivery_sectors row for that name is
    isDeleted=true — i.e. the name is unambiguously a stale/redrawn zone, not a name
    currently reused by an active sector too (a handful of names, e.g. a unit's own
    "Якутск-1"-style placeholder sector, have both — those are NOT excluded).
    Confirmed via delivery/delivery-sectors (docs/dodo-is-openapi): Dodo IS's own
    "Number of sector stops" excludes deleted sectors, since a deleted zone's stop
    keeps "hanging" forever in the raw stop-sales-sectors feed and would otherwise
    inflate stop counts/duration/lost_revenue indefinitely."""
    cur.execute(
        """
        SELECT unit_id, sector_name
        FROM delivery_sectors
        WHERE unit_id = ANY(%s) AND sector_name IS NOT NULL
        GROUP BY unit_id, sector_name
        HAVING bool_and(is_deleted)
        """,
        (list(unit_ids),),
    )
    return {(row["unit_id"], row["sector_name"]) for row in cur.fetchall()}


def load_long_stop_days(cur, event_type: str, unit_ids: set, range_start: date, range_end: date,
                        params: Params = DEFAULT_PARAMS) -> dict:
    """(unit_id, entity_name) -> set of dates where total stop duration that day > 2h.
    entity_name is 'Delivery' for channel stops (baseline is always Delivery-based),
    or the sector name for sector stops."""
    entity_expr = "'Delivery'" if event_type == "channel" else "entity_name"
    cur.execute(
        f"""
        SELECT unit_id, {entity_expr} AS entity_name, started_at_local::date AS day,
               SUM(duration_minutes) AS total_minutes
        FROM stop_events
        WHERE event_type = %s AND unit_id = ANY(%s)
          AND started_at_local::date BETWEEN %s AND %s
          AND duration_minutes IS NOT NULL
        GROUP BY unit_id, entity_name, started_at_local::date
        HAVING SUM(duration_minutes) > %s
        """,
        (event_type, list(unit_ids), range_start, range_end, params.exclude_stop_minutes),
    )
    out = defaultdict(set)
    for row in cur.fetchall():
        out[(row["unit_id"], row["entity_name"])].add(row["day"])
    return out


def avg_prior_same_hour_revenue(revenue_map: dict, long_stop_days: set, day: date, hour: int, lookup_key,
                                 channel_revenue_map: dict, channel_long_stop_days: set, unit_id: str,
                                 params: Params = DEFAULT_PARAMS) -> float:
    values = []
    for i in range(1, params.lookback_weeks + 1):
        d = day - timedelta(weeks=i)
        if d in long_stop_days:
            continue
        v = revenue_map.get(lookup_key(d))
        if v is None:
            continue
        values.append(v)
    if values:
        return sum(values) / len(values)

    # Fallback: 5% × the pizzeria's own Delivery-channel revenue, same hour/weekday,
    # over the same 4 lookback weeks (not the entity's own history — see module
    # docstring). This is the same base used as the primary average for channel
    # stops, so channel-stop fallback and sector-stop fallback share one code path.
    base_values = []
    for i in range(1, params.lookback_weeks + 1):
        d = day - timedelta(weeks=i)
        if d in channel_long_stop_days:
            continue
        v = channel_revenue_map.get((unit_id, d, hour))
        if v is None:
            continue
        base_values.append(v)
    if base_values:
        return params.fallback_share * (sum(base_values) / len(base_values))
    return 0.0


def compute_for_stop(schedule: dict, revenue_map: dict, long_stop_days_map: dict,
                      unit_id: str, entity_name: str | None, is_sector: bool,
                      started_at: datetime, ended_at: datetime,
                      channel_revenue_map: dict, channel_long_stop_days_map: dict,
                      params: Params = DEFAULT_PARAMS) -> tuple[float, float]:
    """(упущенная выручка, минуты стопа в рабочие часы). Второе — то же, что Dodo IS
    показывает в колонках «Время стопов … в рабочие часы»; сырой duration_minutes
    завышает его по секторам до 25%."""
    total = 0.0
    working_minutes = 0.0
    long_stop_days = long_stop_days_map.get((unit_id, "Delivery" if not is_sector else entity_name), set())
    channel_long_stop_days = channel_long_stop_days_map.get((unit_id, "Delivery"), set())
    for day, hour, seg_start, seg_end in hour_segments(started_at, ended_at, schedule, params):
        minutes = (seg_end - seg_start).total_seconds() / 60
        if minutes <= 0:
            continue
        working_minutes += minutes
        if is_sector:
            lookup_key = lambda d: (unit_id, entity_name, d, hour)  # noqa: E731
        else:
            lookup_key = lambda d: (unit_id, d, hour)  # noqa: E731
        avg_rev = avg_prior_same_hour_revenue(
            revenue_map, long_stop_days, day, hour, lookup_key,
            channel_revenue_map, channel_long_stop_days, unit_id, params,
        )
        total += (minutes / 60) * avg_rev
    return round(total, 2), round(working_minutes, 1)


def build_context(cur, event_type: str, from_date: date, to_date: date,
                  params: Params = DEFAULT_PARAMS) -> dict | None:
    """Всё, что нужно для расчёта: стопы периода + справочная выручка + дни с длинным
    стопом. Ничего не пишет — тем же пользуется калибровочный прогон."""
    is_sector = event_type == "sector"
    # "Стопы пиццерии" in Dodo IS only tracks the Delivery channel going down —
    # a full-closure stop also fires simultaneous Dine-in/Takeaway channel rows,
    # which must NOT get their own lost_revenue or the same downtime gets summed
    # 2-3x. Confirmed against a real Dodo IS export (2026-07-22..27): filtering
    # to entity_name='Delivery' matches Dodo's numbers almost exactly, while
    # including all three channels overcounts by up to 2x.
    #
    # entity_category holds Dodo's channelStopType ('Complete' for an actual
    # closure, 'Redirection' for orders rerouted to another unit/channel — not
    # a real loss of demand). Confirmed against a real Dodo IS export
    # (2026-08-17..23, Якутск-2): a Redirection stop was being counted as a
    # full loss, overstating lost_revenue by exactly that stop's amount.
    if event_type == "channel":
        channel_filter = "AND entity_name = 'Delivery'"
        if params.channel_complete_only:
            channel_filter += " AND entity_category = 'Complete'"
    else:
        channel_filter = ""
    cur.execute(
        f"""
        SELECT id, unit_id, entity_name, started_at_local, ended_at_local
        FROM stop_events
        WHERE event_type = %s
          AND ended_at_local IS NOT NULL
          AND started_at_local::date BETWEEN %s AND %s
          {channel_filter}
        """,
        (event_type, from_date, to_date),
    )
    rows = cur.fetchall()
    if not rows:
        return None

    unit_ids = {row["unit_id"] for row in rows}
    excluded_ids = []

    if event_type == "channel":
        # Строки, которые фильтр выше отбросил, но которым расчёт когда-то успел
        # проставить lost_revenue (Dine-in/Takeaway до фикса a2220fc, Redirection —
        # до фикса, добавившего entity_category='Complete'). Пересчёт их не трогает,
        # потому что не выбирает, и они молча продолжают попадать в суммы фронта.
        # Найдено 02.09.2026: одна строка Redirection от 21.08 на 62 851 ₽ давала
        # +12% к неделе 17–23.08 при сверке с Dodo IS.
        cur.execute(
            f"""
            SELECT id FROM stop_events
            WHERE event_type = 'channel' AND lost_revenue IS NOT NULL
              AND started_at_local::date BETWEEN %s AND %s
              AND NOT (TRUE {channel_filter})
            """,
            (from_date, to_date),
        )
        excluded_ids = [row["id"] for row in cur.fetchall()]

    if is_sector and params.exclude_deleted_sectors:
        deleted_sector_names = load_deleted_sector_names(cur, unit_ids)
        excluded_ids = [
            row["id"] for row in rows
            if (row["unit_id"], row["entity_name"]) in deleted_sector_names
        ]
        rows = [row for row in rows if (row["unit_id"], row["entity_name"]) not in deleted_sector_names]

    empty = {"rows": rows, "excluded_ids": excluded_ids, "is_sector": is_sector,
             "revenue_map": {}, "channel_revenue_map": {}, "long_stop_days_map": {},
             "channel_long_stop_days_map": {}, "schedules": {}}
    if not rows:
        return empty

    lookback_start = from_date - timedelta(weeks=params.lookback_weeks)

    if is_sector:
        sector_names = {row["entity_name"] for row in rows if row["entity_name"]}
        revenue_map = load_sector_revenue(cur, unit_ids, sector_names, lookback_start, to_date)
        channel_revenue_map = load_channel_revenue(cur, unit_ids, lookback_start, to_date)
    else:
        revenue_map = load_channel_revenue(cur, unit_ids, lookback_start, to_date)
        channel_revenue_map = revenue_map

    long_stop_days_map = load_long_stop_days(cur, event_type, unit_ids, lookback_start, to_date, params)
    # The fallback base is always the pizzeria's Delivery-channel data, regardless of
    # whether this call is processing channel or sector stops — for channel stops
    # long_stop_days_map already IS this (event_type='channel', entity_name='Delivery').
    channel_long_stop_days_map = (
        long_stop_days_map if not is_sector
        else load_long_stop_days(cur, "channel", unit_ids, lookback_start, to_date, params)
    )
    if not params.long_stop_rule_channel:
        # KB привязывает правило ">2ч" к сектору и подсектору. Снимая его с
        # delivery-канала, снимаем и с базы стопов пиццерии, и с базы фолбэка —
        # это одна и та же выручка.
        channel_long_stop_days_map = {}
        if not is_sector:
            long_stop_days_map = {}

    return {
        "rows": rows,
        "excluded_ids": excluded_ids,
        "is_sector": is_sector,
        "revenue_map": revenue_map,
        "channel_revenue_map": channel_revenue_map,
        "long_stop_days_map": long_stop_days_map,
        "channel_long_stop_days_map": channel_long_stop_days_map,
        "schedules": load_schedules("delivery"),
    }


def compute_updates(ctx: dict, params: Params = DEFAULT_PARAMS) -> list[tuple]:
    """[(stop_id, lost_revenue, working_minutes)] по контексту build_context —
    без записи в БД."""
    updates = []
    for row in ctx["rows"]:
        schedule = ctx["schedules"].get(row["unit_id"].lower(), {})
        lost, minutes = compute_for_stop(
            schedule, ctx["revenue_map"], ctx["long_stop_days_map"],
            row["unit_id"], row["entity_name"], ctx["is_sector"],
            row["started_at_local"], row["ended_at_local"],
            ctx["channel_revenue_map"], ctx["channel_long_stop_days_map"], params,
        )
        updates.append((row["id"], lost, minutes))
    return updates


def process_event_type(cur, event_type: str, from_date: date, to_date: date,
                       params: Params = DEFAULT_PARAMS):
    ctx = build_context(cur, event_type, from_date, to_date, params)
    if ctx is None:
        print(f"Найдено закрытых {event_type}-стопов: 0")
        return
    print(f"Найдено закрытых {event_type}-стопов: {len(ctx['rows'])}")
    if ctx["excluded_ids"]:
        cur.execute(
            "UPDATE stop_events SET lost_revenue = NULL, working_minutes = NULL WHERE id = ANY(%s)",
            (ctx["excluded_ids"],),
        )
        label = "удалённых секторов" if event_type == "sector" else "не участвующих каналов"
        print(f"  обнулено lost_revenue у стопов {label}: {len(ctx['excluded_ids'])}")
    if not ctx["rows"]:
        return
    print(f"  строк выручки: {len(ctx['revenue_map'])}, "
          f"дней с длинным стопом (>{params.exclude_stop_minutes}м): "
          f"{sum(len(v) for v in ctx['long_stop_days_map'].values())}")

    updates = compute_updates(ctx, params)
    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE stop_events AS se
        SET lost_revenue = v.lost_revenue, working_minutes = v.working_minutes
        FROM (VALUES %s) AS v(id, lost_revenue, working_minutes)
        WHERE se.id = v.id
        """,
        updates,
        template="(%s, %s, %s)",
        page_size=1000,
    )
    print(f"  обновлено lost_revenue/working_minutes для {len(updates)} стопов")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="filters by started_at_local date")
    parser.add_argument("--to-date",   required=True)
    args = parser.parse_args()

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    conn = get_connection()
    cur = get_cursor(conn)

    process_event_type(cur, "channel", from_date, to_date)
    process_event_type(cur, "sector", from_date, to_date)

    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
