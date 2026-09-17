# Dodo IS -> PostgreSQL (Supabase): product_daily_revenue (per unit per product per
# sales_channel per day)
#
# Endpoint: accounting/sales — order-level sales, each order has a nested products[]
# list (productId, defaultProductName, price, priceWithDiscount). salesChannel lives
# on the order, so it's propagated to every product line. No pre-aggregated
# per-product-per-day endpoint exists, so we aggregate ourselves.
#
# Used by:
# - compute_lost_revenue.py, to estimate lost revenue from product/ingredient stops
#   (average revenue over the 3 preceding same-weekday occurrences, summed across
#   sales_channel — it doesn't care about the channel split).
# - the pulse-week "Дисконт" metric: discount % = (gross_revenue - revenue) / gross_revenue,
#   broken down by Сеть/По каналам (Доставка vs Ресторан)/По точкам/по категориям.
import os, json, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from collections import defaultdict

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token
from db import get_connection, get_cursor, placeholder

load_dotenv()
BASE = "https://api.dodois.io/dodopizza/ru"
UNITS_FILE = "yakutsk_units.json"


def make_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}"}


def api_get(path: str, params: dict, headers: dict, retries: int = 3) -> dict:
    url = f"{BASE}/{path}"
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
            print(f"  retry {attempt+1}/{retries}: {e}")
            time.sleep(5 * (attempt + 1))
    return {}


def fetch_sales(unit_id: str, day: str, headers: dict) -> list[dict]:
    """Fetch all orders sold on `day` (local time) for a unit, paginated."""
    orders = []
    skip = 0
    take = 1000
    while True:
        data = api_get("accounting/sales", {
            "units": unit_id,
            "from": f"{day}T00:00:00",
            "to": f"{day}T23:59:59",
            "skip": skip,
            "take": take,
        }, headers)
        batch = data.get("sales", [])
        orders.extend(batch)
        if data.get("isEndOfListReached", True):
            break
        skip += take
    return orders


def aggregate_product_revenue(orders: list[dict], day: str, unit_id: str, unit_name: str | None) -> list[dict]:
    # Keyed by (productId, salesChannel) — salesChannel lives on the order, not the
    # product line, so it's propagated down to every product in the order. This is
    # what lets the "Дисконт" metric split by Доставка/Ресторан.
    agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"revenue": 0.0, "gross_revenue": 0.0, "qty": 0, "name": None, "category": None}
    )
    for order in orders:
        channel = order.get("salesChannel") or "Unknown"
        for p in order.get("products", []):
            row = agg[(p["productId"], channel)]
            row["revenue"] += p.get("priceWithDiscount") or 0
            row["gross_revenue"] += p.get("price") or 0
            row["qty"] += 1
            row["name"] = p.get("defaultProductName")
            row["category"] = p.get("productCategoryName")

    return [
        {
            "date": day,
            "unit_id": unit_id,
            "unit_name": unit_name,
            "product_id": product_id,
            "sales_channel": channel,
            "product_name": v["name"],
            "product_category_name": v["category"],
            "revenue": v["revenue"],
            "gross_revenue": v["gross_revenue"],
            "qty": v["qty"],
        }
        for (product_id, channel), v in agg.items()
    ]


def aggregate_hourly_channel_revenue(orders: list[dict], day: str, unit_id: str) -> list[dict]:
    """Net revenue by (hour, sales_channel) — feeds hourly_revenue_by_channel, used by
    compute_stop_lost_revenue.py to estimate lost revenue from "Пиццерия"(channel) stops
    (Dodo IS formula: stop-time × average revenue in the same hour on the same weekday
    over the preceding 4 weeks). orders[].soldAtLocal carries the order's local timestamp,
    so this needs no new endpoint beyond what fetch_sales() already pulls."""
    agg: dict[tuple[int, str], float] = defaultdict(float)
    for order in orders:
        sold_at = order.get("soldAtLocal") or ""
        if len(sold_at) < 13:
            continue
        hour = int(sold_at[11:13])
        channel = order.get("salesChannel") or "Unknown"
        revenue = sum(p.get("priceWithDiscount") or 0 for p in order.get("products", []))
        agg[(hour, channel)] += revenue

    return [
        {"date": day, "hour": hour, "unit_id": unit_id, "sales_channel": channel, "revenue": revenue}
        for (hour, channel), revenue in agg.items()
    ]


def aggregate_channel_revenue(orders: list[dict], day: str, unit_id: str) -> list[dict]:
    """Same net/gross revenue as aggregate_product_revenue, summed across products —
    feeds daily_revenue_by_channel, the pre-aggregated table the "Дисконт" metric
    reads from (product_daily_revenue is per-product, too many rows for the frontend
    to page through — see migrations/013_daily_revenue_by_channel.sql)."""
    agg: dict[str, dict] = defaultdict(lambda: {"revenue": 0.0, "gross_revenue": 0.0})
    for order in orders:
        channel = order.get("salesChannel") or "Unknown"
        row = agg[channel]
        for p in order.get("products", []):
            row["revenue"] += p.get("priceWithDiscount") or 0
            row["gross_revenue"] += p.get("price") or 0

    return [
        {"date": day, "unit_id": unit_id, "sales_channel": channel, **v}
        for channel, v in agg.items()
    ]


def classify_discount(product_name: str | None, category_name: str | None, combo, bonus_action_name: str | None) -> str:
    """Heuristic mapping to DodoIS's discount-report categories — there's no
    exact classification exposed via accounting/sales, so this covers only the
    5 categories with a reliable signal (Combo via the product's own `combo`
    field; Sauces via product name/category; CVM/Customer support/Vouchers via
    bonusActionName prefixes/keywords). Federal/Regional/Local/Dodo coins/HR/B2B
    can't be told apart this way (HR is a manual DodoIS-side annotation not in
    the API at all) — they all fall into the "Маркетинг / другое" catch-all.
    """
    name = (product_name or "").lower()
    cat = (category_name or "").lower()
    if combo is not None:
        return "Комбо"
    if "соус" in name or "соус" in cat:
        return "Соусы в подарок"
    ban = bonus_action_name or ""
    if ban.upper().startswith("CVM"):
        return "CVM"
    if ban.upper().startswith("КЦ"):
        return "Служба поддержки"
    if "опоздан" in ban.lower():
        return "Сертификаты за опоздание"
    return "Маркетинг / другое"


def aggregate_discount_categories(orders: list[dict], day: str, unit_id: str) -> list[dict]:
    agg: dict[str, float] = defaultdict(float)
    for order in orders:
        for p in order.get("products", []):
            price = p.get("price") or 0
            price_disc = p.get("priceWithDiscount") or 0
            amount = price - price_disc
            if amount <= 0:
                continue
            d = p.get("discount") or {}
            category = classify_discount(
                p.get("defaultProductName"), p.get("productCategoryName"),
                p.get("combo"), d.get("bonusActionName"),
            )
            agg[category] += amount

    return [
        {"date": day, "unit_id": unit_id, "category": category, "discount_amount": amount}
        for category, amount in agg.items()
    ]


def upsert_discount_category_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO discount_category_daily (date, unit_id, category, discount_amount)
        VALUES %s
        ON CONFLICT (date, unit_id, category) DO UPDATE SET
            discount_amount = EXCLUDED.discount_amount
    """, [
        (row["date"], row["unit_id"], row["category"], row["discount_amount"])
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def upsert_hourly_channel_revenue_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO hourly_revenue_by_channel (date, hour, unit_id, sales_channel, revenue)
        VALUES %s
        ON CONFLICT (date, hour, unit_id, sales_channel) DO UPDATE SET
            revenue = EXCLUDED.revenue
    """, [
        (row["date"], row["hour"], row["unit_id"], row["sales_channel"], row["revenue"])
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def upsert_channel_revenue_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO daily_revenue_by_channel (date, unit_id, sales_channel, revenue, gross_revenue)
        VALUES %s
        ON CONFLICT (date, unit_id, sales_channel) DO UPDATE SET
            revenue       = EXCLUDED.revenue,
            gross_revenue = EXCLUDED.gross_revenue
    """, [
        (row["date"], row["unit_id"], row["sales_channel"], row["revenue"], row["gross_revenue"])
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SET statement_timeout = 0")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO product_daily_revenue
            (date, unit_id, product_id, sales_channel, product_name, product_category_name, revenue, gross_revenue, qty)
        VALUES %s
        ON CONFLICT (date, unit_id, product_id, sales_channel) DO UPDATE SET
            product_name          = EXCLUDED.product_name,
            product_category_name = EXCLUDED.product_category_name,
            revenue                = EXCLUDED.revenue,
            gross_revenue          = EXCLUDED.gross_revenue,
            qty                    = EXCLUDED.qty
    """, [
        (
            row["date"], row["unit_id"], row["product_id"], row["sales_channel"], row["product_name"],
            row["product_category_name"], row["revenue"], row["gross_revenue"], row["qty"],
        )
        for row in rows
    ], page_size=200)
    conn.commit()
    conn.close()


def date_range(start: str, end: str):
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        yield str(d)
        d += timedelta(days=1)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date",   required=True)
    args = parser.parse_args()

    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    days = list(date_range(args.from_date, args.to_date))
    total = len(days) * len(units)
    done = 0
    done_lock = threading.Lock()

    def fetch_unit(day: str, unit: dict):
        # Each thread gets its own headers/token lookup — get_access_token() is
        # protected by _token_lock (get_sales.py), so this is safe to call concurrently.
        headers = make_headers()
        orders = fetch_sales(unit["id"], day, headers)
        rows = aggregate_product_revenue(orders, day, unit["id"], unit.get("name"))
        category_rows = aggregate_discount_categories(orders, day, unit["id"])
        channel_rows = aggregate_channel_revenue(orders, day, unit["id"])
        hourly_channel_rows = aggregate_hourly_channel_revenue(orders, day, unit["id"])
        return unit, orders, rows, category_rows, channel_rows, hourly_channel_rows

    for day in days:
        print(f"\n[{day}]", flush=True)
        all_rows: list[dict] = []
        all_category_rows: list[dict] = []
        all_channel_revenue_rows: list[dict] = []
        all_hourly_channel_revenue_rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=len(units)) as pool:
            futures = [pool.submit(fetch_unit, day, unit) for unit in units]
            for future in as_completed(futures):
                unit, orders, rows, category_rows, channel_rows, hourly_channel_rows = future.result()
                all_rows.extend(rows)
                all_category_rows.extend(category_rows)
                all_channel_revenue_rows.extend(channel_rows)
                all_hourly_channel_revenue_rows.extend(hourly_channel_rows)
                with done_lock:
                    done += 1
                    print(f"  [{done}/{total}] {unit.get('name')}: {len(orders)} orders -> {len(rows)} products", flush=True)
        upsert_rows(all_rows)
        upsert_discount_category_rows(all_category_rows)
        upsert_channel_revenue_rows(all_channel_revenue_rows)
        upsert_hourly_channel_revenue_rows(all_hourly_channel_revenue_rows)
        print(f"  upserted {len(all_rows)} rows ({len(all_category_rows)} discount-category rows, {len(all_channel_revenue_rows)} channel-revenue rows, {len(all_hourly_channel_revenue_rows)} hourly-channel-revenue rows)", flush=True)

    print("Done.")


if __name__ == "__main__":
    main()
