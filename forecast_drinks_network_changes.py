# Поправка на изменения сети поверх forecast_drinks_procurement.py — сен.2026-янв.2027.
#
# Три события, не отражённые в исторических продажах (см. forecast_drinks_
# procurement.py — тот прогноз молчаливо предполагает неизменный состав сети):
#   1. Якутск-5 (Некрасова, 2А, сейчас только доставка) переезжает; ресторан
#      на новом месте — с 28.09.2026 (сама доставка продолжается без разрыва,
#      поэтому уже учтена в тренде — добавляем только новый канал "ресторан").
#   2. Новая (8-я) точка сети: доставка — с 15.10.2026.
#   3. Та же точка: ресторан — с 29.10.2026.
#
# Масштаб события = средняя сетевая выручка в напитках на одну точку за
# май-июль 2026 (product_daily_revenue x product_classification, канал
# Delivery/Dine-in, только подкатегории Soft drinks/Water/Juce/Ice Tea —
# та же зона, что в forecast_drinks_procurement.py). Раскладка по SKU —
# пропорционально фактическому миксу продукта в этом канале за то же окно
# (ресторанный и доставочный микс напитков заметно различаются).
#
# Даты релокации Якутск-5 и новой точки — из разговора с пользователем
# (2026-08-27), не из системы; при уточнении — поправить EVENTS ниже и
# перезапустить.
import datetime
import json

from forecast_drinks_procurement import load_data, forecast_product, month_index, FORECAST_MONTHS
from db import get_connection, get_cursor

# средняя выручка в напитках на точку/месяц, май-июль 2026 (см. докстринг)
AVG_DELIVERY_PER_UNIT_MONTH = 1527.95
AVG_DINEIN_PER_UNIT_MONTH = 1975.22
DAYS_IN_MONTH = {"2026-09": 30, "2026-10": 31, "2026-11": 30, "2026-12": 31, "2027-01": 31}

EVENTS = [
    {"key": "relocation_dinein", "label": "Якутск-5 — ресторан на новом месте", "channel": "dinein", "start": (2026, 9, 28)},
    {"key": "new_unit_delivery", "label": "Новая точка — доставка", "channel": "delivery", "start": (2026, 10, 15)},
    {"key": "new_unit_dinein", "label": "Новая точка — ресторан", "channel": "dinein", "start": (2026, 10, 29)},
]


def phase_fraction(month_str: str, start_date: tuple) -> float:
    y, m = map(int, month_str.split("-"))
    days_in_month = DAYS_IN_MONTH[month_str]
    month_end = datetime.date(y, m, days_in_month)
    sd = datetime.date(*start_date)
    if sd > month_end:
        return 0.0
    active_start = max(datetime.date(y, m, 1), sd)
    return ((month_end - active_start).days + 1) / days_in_month


def load_channel_mix() -> tuple[dict, dict]:
    """Сетевой микс SKU по каналу продаж (доля от суммы), май-июль 2026 —
    используется, чтобы разложить прибавку события по конкретным напиткам."""
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        select pc.product_name, pdr.sales_channel, sum(pdr.qty) as qty
        from product_daily_revenue pdr
        join product_classification pc on lower(pc.product_uuid) = lower(pdr.product_id)
        where pc.subcategory in ('Soft drinks', 'Water', 'Juce', 'Ice Tea')
          and pdr.sales_channel in ('Delivery', 'Dine-in')
          and pdr.date >= '2026-05-01' and pdr.date < '2026-08-01'
        group by pc.product_name, pdr.sales_channel
    """)
    rows = cur.fetchall()
    conn.close()

    delivery_qty = {r["product_name"]: r["qty"] for r in rows if r["sales_channel"] == "Delivery"}
    dinein_qty = {r["product_name"]: r["qty"] for r in rows if r["sales_channel"] == "Dine-in"}
    delivery_total = sum(delivery_qty.values()) or 1
    dinein_total = sum(dinein_qty.values()) or 1
    return (
        {k: v / delivery_total for k, v in delivery_qty.items()},
        {k: v / dinein_total for k, v in dinein_qty.items()},
    )


def compute_adjustment(by_product: dict) -> dict:
    delivery_share, dinein_share = load_channel_mix()
    adjustment = {product: {} for product in by_product}
    for m in FORECAST_MONTHS:
        for product in by_product:
            add = 0.0
            for ev in EVENTS:
                frac = phase_fraction(m, ev["start"])
                base = AVG_DINEIN_PER_UNIT_MONTH if ev["channel"] == "dinein" else AVG_DELIVERY_PER_UNIT_MONTH
                share = (dinein_share if ev["channel"] == "dinein" else delivery_share).get(product, 0.0)
                add += base * frac * share
            adjustment[product][m] = add
    return adjustment


def main():
    by_product, meta = load_data()
    base_year, base_month = 2023, 9
    last_period_idx = max(month_index(p, base_year, base_month) for h in by_product.values() for p in h)
    adjustment = compute_adjustment(by_product)

    results = []
    for product, history in by_product.items():
        r = forecast_product(history, base_year, base_month, last_period_idx)
        final = {m: (r["forecast"][m] + adjustment[product][m]) if r["forecast"][m] is not None else None for m in FORECAST_MONTHS}
        results.append({"product": product, "baseline": r["forecast"], "network_adjustment": adjustment[product], "forecast_final": final})

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
