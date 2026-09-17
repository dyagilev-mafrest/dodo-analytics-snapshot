# Калибровка формулы упущенной выручки от стопов ПИЦЦЕРИЙ и СЕКТОРОВ.
#
# Гоняет compute_stop_lost_revenue.py с разными Params по одному и тому же периоду,
# ничего не записывая в stop_events, и сравнивает с эталоном Dodo IS из
# dodois_unit_sector_stops (если он загружен — load_dodois_unit_sector_stops.py).
# Аналог analyze_key_ingredients_calibration.py, только там подбиралась
# классификация, а здесь — параметры формулы.
#
# Usage:
#   python analyze_unit_sector_stops_calibration.py --from-date 2026-08-03 --to-date 2026-08-09
#   python analyze_unit_sector_stops_calibration.py --from-date ... --to-date ... --by-unit
#
# Без эталона всё равно полезно: печатает, на сколько ₽ расходятся варианты между
# собой — то есть цену каждого спорного правила методики.
from dataclasses import replace
from datetime import date

from db import get_connection, get_cursor
from compute_stop_lost_revenue import DEFAULT_PARAMS, build_context, compute_updates

# (имя, params) — первый вариант считается текущим поведением.
VARIANTS = [
    ("как сейчас (KB дословно)", DEFAULT_PARAMS),
    ("+ правило >2ч и на пиццерию", replace(DEFAULT_PARAMS, long_stop_rule_channel=True)),
    ("Redirection считаем потерей", replace(DEFAULT_PARAMS, channel_complete_only=False)),
    ("удалённые секторы не исключаем", replace(DEFAULT_PARAMS, exclude_deleted_sectors=False)),
    ("без обрезки рабочим окном", replace(DEFAULT_PARAMS, clip_working_hours=False)),
    ("окно 3 недели", replace(DEFAULT_PARAMS, lookback_weeks=3)),
    ("окно 8 недель", replace(DEFAULT_PARAMS, lookback_weeks=8)),
    ("фолбэк 10% вместо 5%", replace(DEFAULT_PARAMS, fallback_share=0.10)),
    ("без правила >2ч вообще", replace(DEFAULT_PARAMS, exclude_stop_minutes=10 ** 9)),
]


def load_unit_names(cur) -> dict:
    cur.execute("SELECT id, name FROM units")
    return {row["id"]: row["name"] for row in cur.fetchall()}


def load_revenue_by_unit(cur, from_date: date, to_date: date) -> tuple[dict, dict]:
    """(вся выручка по юниту, только Delivery) — два кандидата в знаменатель доли."""
    cur.execute(
        """
        SELECT unit_id, sales_channel, SUM(revenue) AS revenue
        FROM hourly_revenue_by_channel
        WHERE date BETWEEN %s AND %s
        GROUP BY unit_id, sales_channel
        """,
        (from_date, to_date),
    )
    total, delivery = {}, {}
    for row in cur.fetchall():
        rev = float(row["revenue"] or 0)
        total[row["unit_id"]] = total.get(row["unit_id"], 0.0) + rev
        if row["sales_channel"] == "Delivery":
            delivery[row["unit_id"]] = delivery.get(row["unit_id"], 0.0) + rev
    return total, delivery


def load_reference(cur, from_date: date, to_date: date) -> list[dict]:
    cur.execute(
        """
        SELECT unit_name, revenue_rub, lost_pct_unit, lost_pct_sector,
               lost_rub_unit, lost_rub_sector, exported_at
        FROM dodois_unit_sector_stops
        WHERE period_start = %s AND period_end = %s
        ORDER BY unit_name
        """,
        (from_date, to_date),
    )
    return [dict(row) for row in cur.fetchall()]


def run_variant(cur, params, from_date: date, to_date: date) -> dict:
    """{unit_id: {'channel': ₽, 'sector': ₽}} для одного набора параметров."""
    per_unit = {}
    for event_type in ("channel", "sector"):
        ctx = build_context(cur, event_type, from_date, to_date, params)
        if not ctx or not ctx["rows"]:
            continue
        lost_by_id = {stop_id: lost for stop_id, lost, _minutes in compute_updates(ctx, params)}
        for row in ctx["rows"]:
            bucket = per_unit.setdefault(row["unit_id"], {"channel": 0.0, "sector": 0.0})
            bucket[event_type] += lost_by_id.get(row["id"], 0.0)
    return per_unit


def pct_share(lost: float, revenue: float) -> float:
    denom = revenue + lost
    return 100 * lost / denom if denom else 0.0


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--by-unit", action="store_true", help="разбивка по пиццериям для базового варианта")
    args = parser.parse_args()

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)

    conn = get_connection()
    cur = get_cursor(conn)

    unit_names = load_unit_names(cur)
    revenue_total, revenue_delivery = load_revenue_by_unit(cur, from_date, to_date)
    reference = load_reference(cur, from_date, to_date)

    net_revenue = sum(revenue_total.values())
    net_delivery = sum(revenue_delivery.values())

    print(f"\nПериод {from_date}..{to_date} (включительно)")
    print(f"Выручка сети: вся {net_revenue:,.0f} ₽, доставка {net_delivery:,.0f} ₽")

    ref_channel = ref_sector = None
    if reference:
        ref_channel = sum(float(r["lost_rub_unit"] or 0) for r in reference)
        ref_sector = sum(float(r["lost_rub_sector"] or 0) for r in reference)
        exported = {str(r["exported_at"]) for r in reference}
        print(f"Эталон Dodo IS ({len(reference)} юнитов, выгрузка {', '.join(sorted(exported))}): "
              f"пиццерия {ref_channel:,.0f} ₽, сектор {ref_sector:,.0f} ₽")
    else:
        print("Эталон Dodo IS за этот период не загружен "
              "(load_dodois_unit_sector_stops.py) — сравниваю варианты только между собой.")

    base_totals = None
    header = f"\n{'вариант':<34} {'пиццерия ₽':>13} {'сектор ₽':>13} {'итого ₽':>13} {'доля %':>8}"
    if reference:
        header += f" {'% от Dodo IS':>13}"
    else:
        header += f" {'к базовому':>12}"
    print(header)
    print("-" * len(header))

    results = {}
    for name, params in VARIANTS:
        per_unit = run_variant(cur, params, from_date, to_date)
        channel = sum(v["channel"] for v in per_unit.values())
        sector = sum(v["sector"] for v in per_unit.values())
        total = channel + sector
        results[name] = (per_unit, channel, sector)
        line = (f"{name:<34} {channel:>13,.0f} {sector:>13,.0f} {total:>13,.0f} "
                f"{pct_share(total, net_revenue):>7.2f}%")
        if reference:
            ref_total = (ref_channel or 0) + (ref_sector or 0)
            line += f" {(100 * total / ref_total if ref_total else 0):>12.0f}%"
        else:
            if base_totals is None:
                base_totals = total
                line += f" {'—':>12}"
            else:
                line += f" {(100 * total / base_totals if base_totals else 0) - 100:>+11.1f}%"
        print(line)

    # Знаменатель доли — второе неподтверждённое место методики (формула в KB картинкой).
    base_per_unit, base_channel, base_sector = results[VARIANTS[0][0]]
    base_total = base_channel + base_sector
    print(f"\nДоля упущенной выручки, базовый вариант:")
    print(f"  знаменатель = вся выручка + упущенная (как в дашборде): {pct_share(base_total, net_revenue):.2f}%")
    print(f"  знаменатель = только доставка + упущенная:              {pct_share(base_total, net_delivery):.2f}%")

    if args.by_unit or reference:
        ref_by_unit = {r["unit_name"]: r for r in reference}
        print(f"\n{'пиццерия':<14} {'пиццерия ₽':>12} {'сектор ₽':>12} {'доля %':>8}", end="")
        print(f" {'Dodo IS: пиц.':>13} {'сект.':>12} {'откл. итого':>12}" if reference else "")
        for unit_id, vals in sorted(base_per_unit.items(), key=lambda kv: unit_names.get(kv[0], "")):
            name = unit_names.get(unit_id, unit_id[:8])
            unit_total = vals["channel"] + vals["sector"]
            line = (f"{name:<14} {vals['channel']:>12,.0f} {vals['sector']:>12,.0f} "
                    f"{pct_share(unit_total, revenue_total.get(unit_id, 0)):>7.2f}%")
            ref = ref_by_unit.get(name)
            if ref:
                r_ch = float(ref["lost_rub_unit"] or 0)
                r_sec = float(ref["lost_rub_sector"] or 0)
                r_total = r_ch + r_sec
                dev = (100 * unit_total / r_total - 100) if r_total else 0
                line += f" {r_ch:>13,.0f} {r_sec:>12,.0f} {dev:>+11.1f}%"
            elif reference:
                line += f" {'нет в выгрузке':>39}"
            print(line)

    conn.close()


if __name__ == "__main__":
    main()
