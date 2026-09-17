# Прогноз продаж напитков (по SKU, сеть целиком) на сентябрь 2026 - январь 2027
# для отдела закупок.
#
# Источники:
#   - Dodo BI "Dynamic of all metrics by product (P1M)" (Downloads/..._chart.csv) —
#     помесячный qty/revenue по SKU категории "Напитки", сен.2023 - июл.2026
#     (35 месяцев, 3 полных цикла сен-янв).
#   - Dodo BI "Stops of products, details in categories (P1M)"
#     (Downloads/Stops_of_products..._chart.xlsx) — помесячная упущенная выручка
#     от стопов по SKU (только "Обязательный ассортимент"/"Новинка" — так
#     считает сам Dodo IS, см. migrations/040). Используется, чтобы вернуть
#     "истинный" спрос: во время стопа факт продаж = 0, хотя спрос был выше,
#     поэтому тренд без поправки недооценивает продажи у часто стопящихся SKU.
#
# Метод (тренд + сезонность, усреднённая по нескольким годам):
#   0. Поправка на стопы: qty(t) += упущенная_выручка(t) / средняя_цена(t),
#      средняя_цена = revenue(t)/qty(t) из того же CSV за тот же месяц —
#      см. load_data(). Дальше везде используется УЖЕ скорректированный qty.
#   1. Для каждого активного SKU строим лог-линейный тренд qty(t) по всем
#      доступным месяцам (t=0 для сен.2023).
#   2. Сезонный коэффициент месяца = факт(t) / тренд(t) для каждого наблюдения;
#      группируем по календарному месяцу (январь, сентябрь, ...) и берём МЕДИАНУ
#      по всем годам, где есть данные — устойчивее к разовым выбросам, чем
#      единственное наблюдение (см. предыдущую версию на 11 месяцах).
#   3. Прогноз(M, 2026/27) = тренд(t_M) * медианный_сезонный_коэффициент(месяц M).
#      Тот же принцип, что и в compute_revenue_forecast.py/apply_leadership_
#      revenue_plan.py: сохраняем форму (сезонную), масштабируем на тренд.
#   4. SKU без данных за последние 3 месяца выборки считаем снятыми с продажи
#      (сменился формат бутылки/арт — типично для этой категории) и не
#      прогнозируем — они меняют состав ассортимента, а не спрос.
#   5. SKU с < MIN_MONTHS_FOR_TREND точками истории — недостаточно данных.
import csv
import math
import statistics
from collections import defaultdict

SOURCE_CSV = r"C:\Users\123\Downloads\Dynamic_of_all_metrics_by_product__P1M__pivot_table_v2-chart.csv"
STOPS_XLSX = r"C:\Users\123\Downloads\Stops_of_products__details_in_categories_Table__P1M__pivot_table_v2-chart.xlsx"
OUTPUT_XLSX = r"C:\Users\123\Downloads\Прогноз_напитков_сен2026-янв2027.xlsx"

FORECAST_MONTHS = ["2026-09", "2026-10", "2026-11", "2026-12", "2027-01"]
MIN_MONTHS_FOR_TREND = 4
DISCONTINUED_GAP_MONTHS = 3  # нет данных за N последних месяцев выборки -> снят с продажи


def month_index(period: str, base_year: int, base_month: int) -> int:
    y, m = int(period[:4]), int(period[5:7])
    return (y - base_year) * 12 + (m - base_month)


def load_stop_lost_revenue() -> dict[tuple[str, str], float]:
    """(product, period YYYY-MM) -> упущенная выручка, ₽. Только категория
    "Напитки" и только SKU с классификацией "Обязательный ассортимент"/
    "Новинка" (так эту метрику считает сам Dodo IS)."""
    import openpyxl
    wb = openpyxl.load_workbook(STOPS_XLSX, data_only=True)
    ws = wb["Sheet1"]
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        period, cat, product, classification, lost_revenue = row
        if cat != "Напитки" or lost_revenue is None:
            continue
        out[(product, period.strftime("%Y-%m"))] = lost_revenue
    return out


def load_data():
    with open(SOURCE_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    header, data = rows[0], rows[1:]

    by_product = defaultdict(dict)
    revenue_by_product = defaultdict(dict)
    meta = {}
    for row in data:
        period, cat, subcat, metaprod, product, qty, revenue, profit, rating = row
        if cat != "Напитки":
            continue
        by_product[product][period[:7]] = float(qty)
        revenue_by_product[product][period[:7]] = float(revenue)
        meta[product] = subcat

    lost_revenue = load_stop_lost_revenue()
    for (product, period), lost_rev in lost_revenue.items():
        if product not in by_product or period not in by_product[product]:
            continue  # SKU/месяц вне текущей выборки (кофе, снятые с продажи форматы и т.п.)
        qty = by_product[product][period]
        rev = revenue_by_product[product][period]
        if qty <= 0 or rev <= 0:
            continue
        avg_price = rev / qty
        by_product[product][period] = qty + lost_rev / avg_price

    return by_product, meta


def load_classification() -> dict[str, str]:
    """product -> классификация (Обязательный ассортимент/Новинка/Тест/
    Необязательный ассортимент/Распродажа) из product_classification."""
    from db import get_connection, get_cursor
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("select product_name, classification from product_classification where category_name = 'Напитки'")
    rows = cur.fetchall()
    conn.close()
    return {r["product_name"]: r["classification"] for r in rows}


def fit_log_trend(points: list[tuple[int, float]]) -> tuple[float, float]:
    pts = [(t, math.log(q)) for t, q in points if q > 0]
    n = len(pts)
    t_mean = sum(t for t, _ in pts) / n
    y_mean = sum(y for _, y in pts) / n
    num = sum((t - t_mean) * (y - y_mean) for t, y in pts)
    den = sum((t - t_mean) ** 2 for t, _ in pts) or 1e-9
    b = num / den
    a = y_mean - b * t_mean
    return a, b


# Точки сети, открытые ДО начала истории напитков (2023-09) — единственные,
# по которым можно честно сравнить "тот же месяц год назад" на всём окне.
# Якутск-6 (открыта 2024-02) и Якутск-7 (2025-02) появляются НУТРИ окна —
# их ramp-up от 0 до зрелого объёма выглядит как "рост тренда", хотя это
# рост числа точек, а не спроса на существующих.
COMPARABLE_UNIT_IDS = {
    "000d3a240c719a8711e68aba13f953c8",  # Якутск-1
    "000d3a21da51a81211e964acd9bafbe7",  # Якутск-2
    "000d3abf84c3bb2e11ec7ce6666a38d4",  # Якутск-3
    "000d3abf84c3bb2e11ec8faf256493b4",  # Якутск-4
    "9e5cdb331af4833411ed7b5d9f16042c",  # Якутск-5
}


def compute_lfl_wedge(base_year: int = 2023, base_month: int = 9) -> dict:
    """LFL-поправка тренда: разница темпов роста (лог-масштаб, %/мес) между
    ВСЕЙ сетью и только сопоставимыми точками (COMPARABLE_UNIT_IDS) — по
    заказам daily_sales.orders_count, тот же диапазон дат, что и история
    напитков. По заказам (не по выручке) — ближе к динамике штук напитков,
    не искажена ростом среднего чека. Сеть выросла 5->7 точек в этом окне
    (см. COMPARABLE_UNIT_IDS) — "wedge" это то, насколько сетевой тренд
    переоценивает органический рост именно за счёт роста числа точек.
    Считается на реальных daily_sales на момент вызова (не кэш) — если
    сильно разойдётся с log'нутым значением ниже, GitHub issue не заводить,
    просто доверять функции."""
    from db import get_connection, get_cursor
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("""
        select date, unit_id, orders_count from daily_sales
        where date >= '2023-09-01'
    """)
    rows = cur.fetchall()
    conn.close()

    total_by_month = defaultdict(float)
    lfl_by_month = defaultdict(float)
    for r in rows:
        m = str(r["date"])[:7]
        total_by_month[m] += float(r["orders_count"])
        if r["unit_id"] in COMPARABLE_UNIT_IDS:
            lfl_by_month[m] += float(r["orders_count"])

    def fit_b(series: dict) -> float:
        points = [(month_index(p, base_year, base_month), v) for p, v in series.items() if v > 0]
        _, b = fit_log_trend(points)
        return b

    b_total = fit_b(total_by_month)
    b_lfl = fit_b(lfl_by_month)
    return {
        "wedge": b_total - b_lfl,
        "total_growth_pct_per_month": (math.exp(b_total) - 1) * 100,
        "lfl_growth_pct_per_month": (math.exp(b_lfl) - 1) * 100,
        "comparable_units": len(COMPARABLE_UNIT_IDS),
    }


def forecast_product(history: dict[str, float], base_year: int, base_month: int, last_period_idx: int, lfl_wedge: float = 0.0) -> dict:
    periods_sorted = sorted(history)
    last_month_idx = month_index(periods_sorted[-1], base_year, base_month)
    if last_period_idx - last_month_idx > DISCONTINUED_GAP_MONTHS:
        return {"status": "discontinued", "n_months": len(history), "forecast": {m: None for m in FORECAST_MONTHS}}

    points = sorted((month_index(p, base_year, base_month), q) for p, q in history.items())
    n_months = len(points)
    if n_months < MIN_MONTHS_FOR_TREND:
        return {"status": "insufficient_data", "n_months": n_months, "forecast": {m: None for m in FORECAST_MONTHS}}

    a_raw, b_raw = fit_log_trend(points)
    # LFL-поправка: сдвигаем наклон на wedge, сохраняя точку (t_mean, y_mean)
    # исходной регрессии — тренд-линия проходит через тот же "центр тяжести"
    # истории, но с органическим (не раздутым числом точек) наклоном.
    t_mean = sum(t for t, _ in points) / n_months
    b = b_raw - lfl_wedge
    a = a_raw + lfl_wedge * t_mean

    # сезонный коэффициент по календарному месяцу, медиана по всем годам
    factors_by_cal_month = defaultdict(list)
    for period, qty in history.items():
        t = month_index(period, base_year, base_month)
        trend_t = math.exp(a + b * t)
        if trend_t > 0:
            cal_month = int(period[5:7])
            factors_by_cal_month[cal_month].append(qty / trend_t)

    forecast = {}
    n_seasons = {}
    for m in FORECAST_MONTHS:
        cal_month = int(m[5:7])
        t_fwd = month_index(m, base_year, base_month)
        trend_fwd = math.exp(a + b * t_fwd)
        obs = factors_by_cal_month.get(cal_month, [])
        factor = statistics.median(obs) if obs else 1.0
        forecast[m] = trend_fwd * factor
        n_seasons[m] = len(obs)

    min_seasons = min(n_seasons.values())
    status = "ok" if min_seasons >= 2 else ("ok_single_season" if min_seasons == 1 else "trend_only_no_seasonal")

    return {
        "status": status,
        "n_months": n_months,
        "growth_per_month_pct": (math.exp(b) - 1) * 100,
        "growth_per_month_pct_raw": (math.exp(b_raw) - 1) * 100,
        "min_seasons_observed": min_seasons,
        "forecast": forecast,
    }


def main():
    by_product, meta = load_data()
    base_year, base_month = 2023, 9
    last_period_idx = max(
        month_index(p, base_year, base_month) for history in by_product.values() for p in history
    )
    lfl = compute_lfl_wedge(base_year, base_month)
    print(f"LFL-поправка: сеть +{lfl['total_growth_pct_per_month']:.2f}%/мес, "
          f"сопоставимые точки {lfl['lfl_growth_pct_per_month']:+.2f}%/мес, "
          f"wedge {lfl['wedge']:.4f} (лог/мес)")

    results = {p: forecast_product(h, base_year, base_month, last_period_idx, lfl["wedge"]) for p, h in by_product.items()}

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Прогноз"
    ws.append(["Подкатегория", "Продукт", "Статус", "Мес. истории", "Лет сезонности", "Тренд %/мес (LFL)", "Тренд %/мес (сырой)"] + FORECAST_MONTHS)

    def sort_key(p):
        r = results[p]
        total = sum(v or 0 for v in r["forecast"].values())
        return (r["status"] == "discontinued", r["status"] == "insufficient_data", -total)

    for product in sorted(results, key=sort_key):
        r = results[product]
        row = [
            meta[product], product, r["status"], r["n_months"],
            r.get("min_seasons_observed"),
            round(r["growth_per_month_pct"], 1) if "growth_per_month_pct" in r else None,
            round(r["growth_per_month_pct_raw"], 1) if "growth_per_month_pct_raw" in r else None,
        ]
        for m in FORECAST_MONTHS:
            v = r["forecast"].get(m)
            row.append(round(v) if v is not None else None)
        ws.append(row)

    wb.save(OUTPUT_XLSX)
    print(f"Сохранено: {OUTPUT_XLSX}")

    by_status = defaultdict(list)
    for p, r in results.items():
        by_status[r["status"]].append(p)
    for status, products in by_status.items():
        print(f"{status}: {len(products)}")


if __name__ == "__main__":
    main()
