# Дозакрытие «зависших» стоп-событий.
#
# Зачем: get_stops.py запрашивает стопы по ДАТЕ НАЧАЛА и обновляет ended_at_local
# только у тех строк, чья дата начала попала в окно прогона. Ночной job берёт окно
# «7 дней назад → вчера», поэтому стоп, проживший дольше недели, последний раз
# запрашивается ещё до своего закрытия — и его ended_at_local навсегда остаётся NULL,
# а lost_revenue не считается (compute_lost_revenue.py берёт только закрытые стопы).
# На 01.09.2026 в базе так зависло 1462 продуктовых стопа (май 32, июнь 187,
# июль 715, август 528); их досчёт поднимает сходимость с выгрузкой Dodo IS за
# 03-23.08 с 68% до ~90% (см. docs/stops-lost-revenue-reconciliation.md).
#
# Что делает: находит календарные дни, на которых остались незакрытые стопы, и
# перезапрашивает Dodo IS ровно за эти дни (тем же fetch_events, что и get_stops.py),
# апсертя свежие ended_at_local. Дни старше --max-age-days пропускаются: стоп,
# не закрывшийся за два месяца, почти наверняка снят с меню навсегда, и гонять
# по нему API каждую ночь бессмысленно.
import argparse
import json
from datetime import date, timedelta

import httpx

from db import get_connection, get_cursor
from get_stops import UNITS_FILE, fetch_events, make_headers, upsert_rows

EVENT_TYPES = ["product", "ingredient", "channel", "sector"]


def open_stop_dates(cutoff: date) -> list[date]:
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(
        """
        SELECT DISTINCT started_at_local::date AS d
        FROM stop_events
        WHERE ended_at_local IS NULL
          AND event_type = ANY(%s)
          AND started_at_local::date >= %s
        ORDER BY d
        """,
        (EVENT_TYPES, cutoff),
    )
    days = [row["d"] for row in cur.fetchall()]
    conn.close()
    return days


def count_open(cutoff: date) -> int:
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(
        """
        SELECT COUNT(*) AS n FROM stop_events
        WHERE ended_at_local IS NULL AND event_type = ANY(%s) AND started_at_local::date >= %s
        """,
        (EVENT_TYPES, cutoff),
    )
    n = cur.fetchone()["n"]
    conn.close()
    return n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age-days", type=int, default=60,
                        help="не трогать дни старше этого возраста (по умолчанию 60)")
    parser.add_argument("--dry-run", action="store_true", help="только показать, какие дни будут перезапрошены")
    args = parser.parse_args()

    cutoff = date.today() - timedelta(days=args.max_age_days)
    days = open_stop_dates(cutoff)
    if not days:
        print(f"Незакрытых стопов новее {cutoff} нет — нечего дозакрывать.")
        return

    before = count_open(cutoff)
    print(f"Дней с незакрытыми стопами (новее {cutoff}): {len(days)}, незакрытых строк: {before}")
    if args.dry_run:
        print("  " + ", ".join(str(d) for d in days))
        return

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)
    unit_ids = ",".join(u["id"] for u in units)

    failed_days = []
    buffer: list[dict] = []
    for i, day in enumerate(days, 1):
        print(f"[{i}/{len(days)}] {day}", flush=True)
        try:
            rows = fetch_events(unit_ids, str(day), make_headers())
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.ConnectError) as e:
            print(f"  SKIPPING {day} after exhausting retries: {e}", flush=True)
            failed_days.append(str(day))
            continue
        buffer.extend(rows)
        if len(buffer) >= 500:
            upsert_rows(buffer)
            print(f"  upserted {len(buffer)} rows", flush=True)
            buffer = []
    if buffer:
        upsert_rows(buffer)
        print(f"  upserted {len(buffer)} rows", flush=True)

    after = count_open(cutoff)
    print(f"Незакрытых стопов было {before}, стало {after} — дозакрыто {before - after}.")

    if failed_days:
        print(f"Done with {len(failed_days)} failed day(s): {failed_days}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
