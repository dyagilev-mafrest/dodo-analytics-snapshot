# Dodo IS -> PostgreSQL (Supabase): ratings_unit_period
#
# Endpoints (controlling-api):
# - ratings/customer-experience — РКО, рейтинг клиентского опыта
# - ratings/standards           — РС, рейтинг стандартов
#
# Both return the SAME UnitRating shape (rate, avgRate, commonRatingsPosition,
# isRanked) plus the period the rating belongs to, so one loader and one table
# cover both — see migrations/060_ratings_unit_period.sql for why.
#
# Each endpoint returns the CURRENT period only: there are no from/to params.
# History therefore accumulates run by run; past periods would need
# /ratings/{kind}/history, which is a separate job.
#
# Re-running within the same period is harmless: the upsert is keyed by
# (kind, unit_id, period_from) and overwrites the row. That matters because
# publishStatus goes Calculated -> published and rates move after appeals, so
# the latest pull for a period is the one we want to keep.
#
# Usage:
#   python get_ratings.py            # both ratings
#   python get_ratings.py --kind standards
import json

import httpx
import psycopg2.extras
from dotenv import load_dotenv

from get_sales import get_access_token
from db import get_connection, get_cursor

load_dotenv()
BASE = "https://api.dodois.io/controlling"
UNITS_FILE = "yakutsk_units.json"
KINDS = ("customer-experience", "standards")
PAGE = 1000  # API cap for take


def make_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def api_get(url: str, params: dict, headers: dict, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=60)
            if r.status_code == 401:
                stale = headers.get("Authorization", "").removeprefix("Bearer ")
                headers["Authorization"] = f"Bearer {get_access_token(force_refresh=True, _stale_token=stale)}"
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            if attempt == retries - 1:
                raise
            print(f"  повтор после {type(e).__name__}")
    raise RuntimeError("unreachable")


def load_units() -> str:
    # До 30 заведений на запрос, строго через запятую без пробелов. Якутских 7,
    # так что режем только на случай будущего роста сети.
    units = json.load(open(UNITS_FILE, encoding="utf-8"))
    return ",".join(u["id"] for u in units)


def fetch_kind(kind: str, units: str, headers: dict) -> list[dict]:
    """Все страницы текущего периода. Пагинация — skip/take до isEndOfListReached."""
    rows, skip = [], 0
    while True:
        data = api_get(f"{BASE}/ratings/{kind}", {"units": units, "take": PAGE, "skip": skip}, headers)
        period_from, period_to = data["periodFrom"], data["periodTo"]
        status, published_at = data["publishStatus"], data.get("publishedAt")
        for u in data.get("unitRates") or []:
            rows.append({
                "kind": kind,
                # API отдаёт UUID в верхнем регистре, остальные таблицы хранят
                # нижний — без нормализации джойны по unit_id молча не сойдутся.
                "unit_id": u["unitId"].lower(),
                "unit_name": u["unitName"],
                "period_from": period_from,
                "period_to": period_to,
                "rate": u.get("rate"),
                "avg_rate": u.get("avgRate"),
                "common_position": u.get("commonRatingsPosition"),
                "is_ranked": u.get("isRanked"),
                "publish_status": status,
                "published_at": published_at,
            })
        if data.get("isEndOfListReached", True):
            break
        skip += PAGE
    return rows


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO ratings_unit_period
            (kind, unit_id, unit_name, period_from, period_to, rate, avg_rate,
             common_position, is_ranked, publish_status, published_at)
        VALUES %s
        ON CONFLICT (kind, unit_id, period_from) DO UPDATE SET
            unit_name       = EXCLUDED.unit_name,
            period_to       = EXCLUDED.period_to,
            rate            = EXCLUDED.rate,
            avg_rate        = EXCLUDED.avg_rate,
            common_position = EXCLUDED.common_position,
            is_ranked       = EXCLUDED.is_ranked,
            publish_status  = EXCLUDED.publish_status,
            published_at    = EXCLUDED.published_at,
            loaded_at       = now()
    """, [(
        r["kind"], r["unit_id"], r["unit_name"], r["period_from"], r["period_to"],
        r["rate"], r["avg_rate"], r["common_position"], r["is_ranked"],
        r["publish_status"], r["published_at"],
    ) for r in rows])
    conn.commit()
    cur.close()
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=KINDS, help="только один рейтинг вместо обоих")
    args = parser.parse_args()

    units = load_units()
    headers = make_headers()
    for kind in ([args.kind] if args.kind else KINDS):
        rows = fetch_kind(kind, units, headers)
        upsert_rows(rows)
        period = f'{rows[0]["period_from"]}…{rows[0]["period_to"]}' if rows else "—"
        graded = sum(1 for r in rows if r["rate"] is not None)
        print(f"{kind}: {len(rows)} пиццерий за {period}, с оценкой {graded}, "
              f"статус {rows[0]['publish_status'] if rows else '—'}")


if __name__ == "__main__":
    main()
