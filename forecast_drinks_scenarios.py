# Сценарный прогноз напитков (консервативный/базовый/оптимистичный) + ABC-анализ
# по SKU, сен.2026-янв.2027 — надстройка над forecast_drinks_procurement.py и
# forecast_drinks_network_changes.py.
#
# Два независимых источника неопределённости объединяются в три сценария:
#   1. Тренд по SKU — регрессия даёт не только точку, но и стандартную ошибку
#      прогноза; консервативный/оптимистичный сценарий берёт тренд минус/плюс
#      1 SE (~68% доверительный интервал в лог-пространстве), сезонный
#      коэффициент не пересчитывается (он и так однонаблюдательный у части SKU).
#   2. Изменения сети (Якутск-5 + новая точка, см. forecast_drinks_network_
#      changes.py) — консервативный сценарий добавляет ramp-up (0.5x первый
#      месяц канала, 0.75x второй, дальше полностью), оптимистичный использует
#      бенчмарк не "средняя по сети", а среднее двух лучших точек по каналу.
#
# ABC — по суммарному прогнозному объёму (базовый сценарий, 5 месяцев) среди
# активных прогнозируемых SKU: A — куда попадает первые 80% накопленного
# объёма, B — 80-95%, C — остальное. Выбран объём (не выручка) — то, что
# закупки физически считают и хранят.
import datetime
import json
import math

from forecast_drinks_procurement import load_data, month_index, FORECAST_MONTHS, MIN_MONTHS_FOR_TREND, compute_lfl_wedge
from forecast_drinks_network_changes import (
    load_channel_mix, phase_fraction, DAYS_IN_MONTH,
    AVG_DELIVERY_PER_UNIT_MONTH, AVG_DINEIN_PER_UNIT_MONTH, EVENTS,
)

Z = 1.0  # ±1 стандартная ошибка регрессии ≈ 68% интервал


def fit_log_trend_with_se(points: list[tuple[int, float]]):
    pts = [(t, math.log(q)) for t, q in points if q > 0]
    n = len(pts)
    t_mean = sum(t for t, _ in pts) / n
    y_mean = sum(y for _, y in pts) / n
    sxx = sum((t - t_mean) ** 2 for t, _ in pts) or 1e-9
    b = sum((t - t_mean) * (y - y_mean) for t, y in pts) / sxx
    a = y_mean - b * t_mean
    sse = sum((y - (a + b * t)) ** 2 for t, y in pts)
    df = max(n - 2, 1)
    mse = sse / df
    return a, b, mse, sxx, t_mean, n


def predict_with_band(a, b, mse, sxx, t_mean, n, t0):
    mid = a + b * t0
    se_pred = math.sqrt(max(mse, 0) * (1 + 1 / n + (t0 - t_mean) ** 2 / sxx))
    return math.exp(mid - Z * se_pred), math.exp(mid), math.exp(mid + Z * se_pred)


def forecast_scenarios(history: dict, base_year: int, base_month: int, last_period_idx: int, lfl_wedge: float = 0.0) -> dict:
    periods_sorted = sorted(history)
    last_month_idx = month_index(periods_sorted[-1], base_year, base_month)
    n_months = len(history)

    if last_period_idx - last_month_idx > 3:
        return {"status": "discontinued", "n_months": n_months, "low": {}, "mid": {}, "high": {}}
    if n_months < MIN_MONTHS_FOR_TREND:
        return {"status": "insufficient_data", "n_months": n_months, "low": {}, "mid": {}, "high": {}}

    points = sorted((month_index(p, base_year, base_month), q) for p, q in history.items())
    a, b, mse, sxx, t_mean, n = fit_log_trend_with_se(points)
    # LFL-поправка (см. forecast_drinks_procurement.compute_lfl_wedge) — сдвигаем
    # наклон, ширину доверительной полосы (mse/sxx) не трогаем.
    b -= lfl_wedge
    a += lfl_wedge * t_mean

    factors_by_cal_month = {}
    for period, qty in history.items():
        t = month_index(period, base_year, base_month)
        trend_t = math.exp(a + b * t)
        if trend_t > 0:
            factors_by_cal_month.setdefault(int(period[5:7]), []).append(qty / trend_t)

    import statistics
    low, mid, high = {}, {}, {}
    min_seasons = None
    for m in FORECAST_MONTHS:
        cal_month = int(m[5:7])
        t_fwd = month_index(m, base_year, base_month)
        trend_low, trend_mid, trend_high = predict_with_band(a, b, mse, sxx, t_mean, n, t_fwd)
        obs = factors_by_cal_month.get(cal_month, [])
        factor = statistics.median(obs) if obs else 1.0
        low[m], mid[m], high[m] = trend_low * factor, trend_mid * factor, trend_high * factor
        seasons = len(obs)
        min_seasons = seasons if min_seasons is None else min(min_seasons, seasons)

    status = "ok" if min_seasons >= 2 else ("ok_single_season" if min_seasons == 1 else "trend_only_no_seasonal")
    return {"status": status, "n_months": n_months, "min_seasons": min_seasons, "low": low, "mid": mid, "high": high}


def network_adjustment_scenarios(by_product: dict) -> dict:
    delivery_share, dinein_share = load_channel_mix()

    # оптимистичный бенчмарк = среднее двух лучших точек по каналу (см. докстринг)
    from db import get_connection, get_cursor
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        select pdr.unit_id, pdr.sales_channel, sum(pdr.qty) as qty
        from product_daily_revenue pdr
        join product_classification pc on lower(pc.product_uuid) = lower(pdr.product_id)
        where pc.subcategory in ('Soft drinks', 'Water', 'Juce', 'Ice Tea')
          and pdr.sales_channel in ('Delivery', 'Dine-in')
          and pdr.date >= '2026-05-01' and pdr.date < '2026-08-01'
        group by pdr.unit_id, pdr.sales_channel
    """)
    rows = cur.fetchall()
    conn.close()
    by_channel = {"Delivery": [], "Dine-in": []}
    for r in rows:
        by_channel[r["sales_channel"]].append(r["qty"] / 3)
    top2_delivery = sum(sorted(by_channel["Delivery"])[-2:]) / 2
    top2_dinein = sum(sorted(by_channel["Dine-in"])[-2:]) / 2

    def ramp_mult(month_str, start_date):
        y, m = map(int, month_str.split("-"))
        sy, sm, _ = start_date
        months_since = (y - sy) * 12 + (m - sm)
        return {0: 0.5, 1: 0.75}.get(months_since, 1.0)

    def per_scenario_total(scenario):
        totals = {}
        for m in FORECAST_MONTHS:
            total = 0.0
            for ev in EVENTS:
                frac = phase_fraction(m, ev["start"])
                if frac == 0:
                    continue
                if scenario == "conservative":
                    base = AVG_DINEIN_PER_UNIT_MONTH if ev["channel"] == "dinein" else AVG_DELIVERY_PER_UNIT_MONTH
                    total += base * frac * ramp_mult(m, ev["start"])
                elif scenario == "optimistic":
                    base = top2_dinein if ev["channel"] == "dinein" else top2_delivery
                    total += base * frac
                else:
                    base = AVG_DINEIN_PER_UNIT_MONTH if ev["channel"] == "dinein" else AVG_DELIVERY_PER_UNIT_MONTH
                    total += base * frac
            totals[m] = total
        return totals

    def per_product_adjustment(scenario):
        adj = {p: {} for p in by_product}
        for m in FORECAST_MONTHS:
            for ev in EVENTS:
                frac = phase_fraction(m, ev["start"])
                if frac == 0:
                    continue
                if scenario == "conservative":
                    base = AVG_DINEIN_PER_UNIT_MONTH if ev["channel"] == "dinein" else AVG_DELIVERY_PER_UNIT_MONTH
                    scaled = base * frac * ramp_mult(m, ev["start"])
                elif scenario == "optimistic":
                    base = top2_dinein if ev["channel"] == "dinein" else top2_delivery
                    scaled = base * frac
                else:
                    base = AVG_DINEIN_PER_UNIT_MONTH if ev["channel"] == "dinein" else AVG_DELIVERY_PER_UNIT_MONTH
                    scaled = base * frac
                share_map = dinein_share if ev["channel"] == "dinein" else delivery_share
                for product in by_product:
                    adj[product].setdefault(m, 0.0)
                    adj[product][m] += scaled * share_map.get(product, 0.0)
        return adj

    return {
        "top2_delivery_per_unit_month": top2_delivery,
        "top2_dinein_per_unit_month": top2_dinein,
        "network_total": {s: per_scenario_total(s) for s in ("conservative", "base", "optimistic")},
        "product_adjustment": {s: per_product_adjustment(s) for s in ("conservative", "base", "optimistic")},
    }


def compute_abc(products: list[dict]) -> dict:
    eligible = [p for p in products if p["status"] not in ("discontinued", "insufficient_data")]
    totals = {p["product"]: sum(p["mid"].values()) for p in eligible}
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    grand_total = sum(v for _, v in ranked) or 1
    abc = {}
    running = 0.0
    for name, v in ranked:
        running += v
        cum_pct = running / grand_total * 100
        tier = "A" if cum_pct <= 80 else ("B" if cum_pct <= 95 else "C")
        abc[name] = {"total": v, "cum_pct": cum_pct, "tier": tier}
    return abc


def main():
    by_product, meta = load_data()
    base_year, base_month = 2023, 9
    last_period_idx = max(month_index(p, base_year, base_month) for h in by_product.values() for p in h)
    wedge = compute_lfl_wedge(base_year, base_month)["wedge"]

    per_product = {p: forecast_scenarios(h, base_year, base_month, last_period_idx, wedge) for p, h in by_product.items()}
    net = network_adjustment_scenarios(by_product)
    abc = compute_abc([{"product": p, **r} for p, r in per_product.items()])

    out = []
    for product, r in per_product.items():
        row = {"product": product, "subcategory": meta[product], "status": r["status"], "n_months": r["n_months"],
               "min_seasons": r.get("min_seasons"), "abc": abc.get(product)}
        for scenario in ("conservative", "base", "optimistic"):
            key = {"conservative": "low", "base": "mid", "optimistic": "high"}[scenario]
            trend_part = r.get(key, {})
            adj_part = net["product_adjustment"][scenario].get(product, {})
            row[scenario] = {m: (trend_part[m] + adj_part.get(m, 0.0)) if m in trend_part else None for m in FORECAST_MONTHS}
        out.append(row)

    print(json.dumps({"products": out, "network": net}, ensure_ascii=False))


if __name__ == "__main__":
    main()
