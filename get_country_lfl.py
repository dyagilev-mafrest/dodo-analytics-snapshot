# Dodo IS -> PostgreSQL (Supabase): country_lfl (LFL заказов/выручки по стране)
#
# Endpoint: customer-feedback/lfl/by-countries — Dodo IS уже считает LFL сам
# (рост к тому же периоду год назад), нам не нужно ничего агрегировать из
# сырых заказов. Один запрос на гранулярность (day/week/month) отдаёт сразу
# весь запрошенный диапазон дат, без пагинации.
#
# Используется как серия "РФ" в переключателе чарта "LFL заказов" (блок
# "Продажи", dodo-analytics-dashboard) — данных по регионам/ДФО в API нет
# (проверено: accounting/sales отдаёт 403 на чужие юниты, отдельных
# эндпоинтов lfl/by-regions или похожих не существует).
import os

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token
from db import get_connection, get_cursor

load_dotenv()
BASE = "https://api.dodois.io/customer-feedback"
COUNTRY_ID = os.getenv("DODO_COUNTRY", "ru").lower()
GRANULARITIES = ["day", "week", "month"]


def make_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}"}


def fetch_lfl(granularity: str, from_date: str, to_date: str, headers: dict) -> list[dict]:
    r = httpx.get(f"{BASE}/lfl/by-countries", params={
        "countries": COUNTRY_ID.upper(),
        "from": from_date,
        "to": to_date,
        "granularity": granularity,
    }, headers=headers, timeout=60)
    if r.status_code == 401:
        headers["Authorization"] = f"Bearer {get_access_token(force_refresh=True)}"
        r = httpx.get(f"{BASE}/lfl/by-countries", params={
            "countries": COUNTRY_ID.upper(),
            "from": from_date,
            "to": to_date,
            "granularity": granularity,
        }, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json().get("lfl", [])


def upsert_rows(granularity: str, rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO country_lfl (country_id, granularity, date, lfl_orders, lfl_revenue)
        VALUES %s
        ON CONFLICT (country_id, granularity, date) DO UPDATE SET
            lfl_orders  = EXCLUDED.lfl_orders,
            lfl_revenue = EXCLUDED.lfl_revenue
    """, [
        (COUNTRY_ID, granularity, row["date"], row["lflOrder"], row["lflRevenue"])
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default="2016-01-01")
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    headers = make_headers()
    for granularity in GRANULARITIES:
        rows = fetch_lfl(granularity, args.from_date, args.to_date, headers)
        upsert_rows(granularity, rows)
        print(f"{granularity}: upserted {len(rows)} rows", flush=True)

    print("Done.")


if __name__ == "__main__":
    main()
