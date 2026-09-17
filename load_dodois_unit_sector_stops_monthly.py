# Ручные экспорты Dodo IS Superset -> dodois_unit_sector_stops_monthly.
#
# Пара выгрузок по стопам ПИЦЦЕРИЙ и СЕКТОРОВ, сеть целиком, помесячно:
#   rub — «Динамика упущенной выручки (стопы пиццерий new)»:
#         Период | по стопам пиццерии | по стопам сектор | по стопам подсектор
#   pct — «Динамика % упущенной выручки (все стопы)»:
#         Период | Доля упущенной выручки по стопам пиццерия + сектор | 52 weeks ago
#         (доля приходит долей от 1; колонка «52 weeks ago» игнорируется — это
#          тот же ряд со сдвигом, и он у нас уже есть своими строками)
#
# Доли по уровням ВЫВОДЯТСЯ здесь: выгрузка даёт рубли по трём уровням, а долю
# только общую. Знаменатель общей доли — «выручка + упущенное», поэтому доля
# уровня = pct_total * rub_уровня / rub_всего. См. migrations/068.
#
# Не API-загрузка, расписания нет — запускать руками при новой паре выгрузок.
#
# Usage:
#   python load_dodois_unit_sector_stops_monthly.py <rub.csv> <pct.csv>
#   ... --dry-run
import csv
import io
import sys
from datetime import datetime

from db import get_connection, get_cursor


def read_csv(path: str) -> list[tuple]:
    """(месяц, [числа колонок]) — Superset отдаёт «;», BOM и точку в дробях."""
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    out = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        month = datetime.strptime(row[0].strip()[:10], "%Y-%m-%d").date().replace(day=1)
        out.append((month, [float(c) if (c or "").strip() else None for c in row[1:]]))
    return out


def build(rub_path: str, pct_path: str) -> list[dict]:
    rub = {m: v for m, v in read_csv(rub_path)}
    pct = {m: v for m, v in read_csv(pct_path)}

    rows = []
    for month in sorted(set(rub) | set(pct)):
        levels = rub.get(month, [])
        unit = levels[0] if len(levels) > 0 else None
        sector = levels[1] if len(levels) > 1 else None
        subsector = levels[2] if len(levels) > 2 else None

        total_pct = None
        shares = pct.get(month)
        if shares and shares[0] is not None:
            total_pct = shares[0] * 100

        # Доля уровня — из его рублей в общей сумме. Считаем только когда есть
        # и общая доля, и ненулевые рубли: иначе делили бы на ноль в месяце без
        # единого стопа, а ноль там законный результат, а не ошибка.
        total_rub = sum(v for v in (unit, sector) if v)
        unit_pct = sector_pct = None
        if total_pct is not None and total_rub:
            unit_pct = total_pct * (unit or 0) / total_rub
            sector_pct = total_pct * (sector or 0) / total_rub
        elif total_pct == 0:
            unit_pct = sector_pct = 0.0

        rows.append({
            "month": month,
            "lost_rub_unit": unit, "lost_rub_sector": sector, "lost_rub_subsector": subsector,
            "lost_pct_total": total_pct, "lost_pct_unit": unit_pct, "lost_pct_sector": sector_pct,
        })
    return rows


def upsert(rows: list[dict]):
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO dodois_unit_sector_stops_monthly
            (month, lost_rub_unit, lost_rub_sector, lost_rub_subsector,
             lost_pct_total, lost_pct_unit, lost_pct_sector)
        VALUES %s
        ON CONFLICT (month) DO UPDATE SET
            lost_rub_unit      = EXCLUDED.lost_rub_unit,
            lost_rub_sector    = EXCLUDED.lost_rub_sector,
            lost_rub_subsector = EXCLUDED.lost_rub_subsector,
            lost_pct_total     = EXCLUDED.lost_pct_total,
            lost_pct_unit      = EXCLUDED.lost_pct_unit,
            lost_pct_sector    = EXCLUDED.lost_pct_sector,
            loaded_at          = now()
    """, [(r["month"], r["lost_rub_unit"], r["lost_rub_sector"], r["lost_rub_subsector"],
           r["lost_pct_total"], r["lost_pct_unit"], r["lost_pct_sector"]) for r in rows],
        page_size=200)
    conn.commit()
    conn.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print("Usage: python load_dodois_unit_sector_stops_monthly.py <rub.csv> <pct.csv>")
        sys.exit(1)
    rows = build(args[0], args[1])

    print(f"{len(rows)} месяцев: {rows[0]['month']} - {rows[-1]['month']}")
    print("месяц        пиццерии          секторы         всего")
    for r in rows:
        pu = "-" if r["lost_pct_unit"] is None else f"{r['lost_pct_unit']:.2f}%"
        ps = "-" if r["lost_pct_sector"] is None else f"{r['lost_pct_sector']:.2f}%"
        pt = "-" if r["lost_pct_total"] is None else f"{r['lost_pct_total']:.2f}%"
        print(f"  {r['month']}  {(r['lost_rub_unit'] or 0):>12,.0f} {pu:>8}"
              f"  {(r['lost_rub_sector'] or 0):>12,.0f} {ps:>8}  {pt:>8}")

    if "--dry-run" in sys.argv:
        print("--dry-run: в БД не пишем.")
        return
    upsert(rows)
    print(f"Загружено {len(rows)} месяцев в dodois_unit_sector_stops_monthly.")


if __name__ == "__main__":
    main()
