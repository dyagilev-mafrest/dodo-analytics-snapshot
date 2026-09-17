# Retrospective revenue forecast baseline for the dashboard's Revenue/Сеть chart
# (dodo-analytics-dashboard) — "what would we normally have expected for this day",
# to compare against actual.
#
# Method (reverse-engineered empirically against the third-party "Автографик" tool,
# see project memory/grilling notes): median of the same weekday over the nearest
# non-holiday weeks, with an outlier trim as a safety net for anything not on the
# hardcoded holiday calendar (local one-off events, promotions, etc).
#
# A plain trailing average badly overshoots any target week whose lookback window
# crosses a holiday (New Year's alone inflates it by +15-25% — verified against
# real Jan 2026 weeks). Pure derived table — computed entirely from daily_sales
# already in Supabase, no Dodo IS API calls, safe to (re)run any time.
import statistics
from datetime import date, timedelta

from db import get_connection, get_cursor

# Generous windows around RU federal holidays — used only to exclude lookback
# weeks from the forecast baseline, not for exact non-working-day rules, so being
# off by a day or two doesn't matter. Extend this list as new years are needed.
HOLIDAY_RANGES = [
    (date(2023, 12, 23), date(2024, 1, 8)),   # НГ/Рождество 2023-2024
    (date(2024, 2, 21), date(2024, 2, 25)),   # 23 февраля
    (date(2024, 3, 7),  date(2024, 3, 10)),   # 8 марта
    (date(2024, 4, 27), date(2024, 5, 1)),    # майские (1 блок)
    (date(2024, 5, 8),  date(2024, 5, 12)),   # майские (2 блок)
    (date(2024, 6, 11), date(2024, 6, 13)),   # 12 июня
    (date(2024, 11, 2), date(2024, 11, 4)),   # 4 ноября

    (date(2024, 12, 22), date(2025, 1, 8)),   # НГ/Рождество 2024-2025
    (date(2025, 2, 21), date(2025, 2, 24)),   # 23 февраля
    (date(2025, 3, 7),  date(2025, 3, 10)),   # 8 марта
    (date(2025, 4, 30), date(2025, 5, 4)),    # майские (1 блок)
    (date(2025, 5, 8),  date(2025, 5, 11)),   # майские (2 блок)
    (date(2025, 6, 11), date(2025, 6, 15)),   # 12 июня
    (date(2025, 11, 1), date(2025, 11, 4)),   # 4 ноября

    (date(2025, 12, 15), date(2026, 1, 11)),  # НГ/Рождество 2025-2026 (проверено эмпирически)
    (date(2026, 2, 21), date(2026, 2, 23)),   # 23 февраля
    (date(2026, 3, 7),  date(2026, 3, 9)),    # 8 марта
    (date(2026, 4, 30), date(2026, 5, 3)),    # майские (1 блок)
    (date(2026, 5, 8),  date(2026, 5, 11)),   # майские (2 блок)
    (date(2026, 6, 12), date(2026, 6, 14)),   # 12 июня
    (date(2026, 11, 2), date(2026, 11, 4)),   # 4 ноября
]

COLLECT_WEEKS = 8       # how many non-holiday lookback weeks to gather
MAX_LOOKBACK_WEEKS = 20  # give up searching further back than this
TRIM_FRACTION = 0.30     # drop candidates deviating >30% from the collected median


def is_holiday(d: date) -> bool:
    return any(start <= d <= end for start, end in HOLIDAY_RANGES)


def forecast_for(daily: dict, unit_id: str, target: date) -> float | None:
    candidates = []
    k = 1
    while len(candidates) < COLLECT_WEEKS and k <= MAX_LOOKBACK_WEEKS:
        prev = target - timedelta(weeks=k)
        if not is_holiday(prev):
            v = daily.get((unit_id, prev))
            if v is not None:
                candidates.append(v)
        k += 1
    if not candidates:
        return None
    med = statistics.median(candidates)
    survivors = [v for v in candidates if med and abs(v - med) / med <= TRIM_FRACTION]
    return statistics.median(survivors) if survivors else med


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    conn = get_connection()
    cur = get_cursor(conn)

    # Load ALL daily_sales once — cheap (a few thousand rows/unit), avoids a
    # DB round trip per (unit, day) lookback lookup.
    cur.execute("SELECT date, unit_id, sales FROM daily_sales")
    daily = {}
    for row in cur.fetchall():
        d = row["date"] if isinstance(row["date"], date) else date.fromisoformat(row["date"])
        daily[(row["unit_id"], d)] = float(row["sales"])

    units = sorted({uid for uid, _ in daily.keys()})

    from_d = date.fromisoformat(args.from_date)
    to_d = date.fromisoformat(args.to_date)

    rows = []
    d = from_d
    while d <= to_d:
        for unit_id in units:
            fc = forecast_for(daily, unit_id, d)
            if fc is not None:
                rows.append((d, unit_id, round(fc, 2)))
        d += timedelta(days=1)

    import psycopg2.extras
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO revenue_forecast_daily (date, unit_id, forecast_revenue)
        VALUES %s
        ON CONFLICT (date, unit_id) DO UPDATE SET
            forecast_revenue = EXCLUDED.forecast_revenue
    """, rows, page_size=500)
    conn.commit()
    conn.close()

    print(f"Upserted {len(rows)} rows ({from_d} -> {to_d}, {len(units)} units).")


if __name__ == "__main__":
    main()
