# Loads the manually-exported Dodo IS figures for the "Стопы пиццерий и активность
# секторов доставки" dashboard into dodois_unit_sector_stops — эталон, против которого
# analyze_unit_sector_stops_calibration.py гоняет варианты нашей формулы.
# See migrations/054_dodois_unit_sector_stops.sql.
#
# Виджет «Детализация стопов», выгрузка table-chart: строка = период × пиццерия,
# колонки — доли и ₽ по стопам пиццерии/секторов/подсекторов, кол-во стопов, время
# стопов в рабочие часы и выручка.
#
# Not an API pull — no scheduled job; re-run manually whenever a fresh export is
# provided. ON CONFLICT перезаписывает уже загруженный период (цифра Dodo IS дозревает
# неделями — повторная выгрузка того же периода это норма, см.
# docs/stops-lost-revenue-reconciliation.md).
#
# Usage:
#   python load_dodois_unit_sector_stops.py export.xlsx --exported-at 2026-09-02
#   python load_dodois_unit_sector_stops.py export.csv  --exported-at 2026-09-02 \
#       --period-start 2026-08-03 --period-end 2026-08-09
#
# Колонки ищутся по заголовку (регистр и лишние пробелы не важны, достаточно
# вхождения подстроки, выигрывает самый длинный алиас — иначе «…подсектор» съела бы
# колонку «…сектор»). Незнакомые колонки игнорируются, но печатаются, чтобы не
# потерять данные молча.
#
# Доли принимаются и как доля единицы (0.0195), и как проценты (1.95) — различаются
# по максимуму в колонке. ₽ выводится из доли и выручки, если в выгрузке нет
# абсолютных значений: share = lost / (lost + revenue) => lost = share*revenue/(1-share).
import csv
from datetime import date, datetime, timedelta

import psycopg2.extras

from db import get_connection, get_cursor

COLUMN_ALIASES = {
    "period":              ["период", "неделя", "week", "месяц", "month", "дата"],
    "unit_name":           ["пиццерия", "unit", "подразделение"],
    "revenue_rub":         ["выручка"],
    "lost_pct_total":      ["доля упущенной выручки по стопам пиццерия + сектор",
                            "доля упущенной выручки от стопов пиццерий + секторов"],
    "lost_pct_unit":       ["доля упущенной выручки по стопам пиццерии",
                            "доля упущенной выручки от стопов пиццерий"],
    "lost_pct_sector":     ["доля упущенной выручки по стопам секторов",
                            "доля упущенной выручки по стопам сектор"],
    "lost_pct_subsector":  ["доля упущенной выручки по стопам подсекторов",
                            "доля упущенной выручки по стопам подсектор"],
    "lost_rub_total":      ["упущенная выручка от стопов пиццерий + секторов",
                            "упущенная выручка по стопам пиццерия + сектор"],
    "lost_rub_unit":       ["упущенная выручка по стопам пиццерии",
                            "упущенная выручка от стопов пиццерий"],
    "lost_rub_sector":     ["упущенная выручка по стопам сектор"],
    "lost_rub_subsector":  ["упущенная выручка по стопам подсектор"],
    "stop_minutes_unit":      ["время стопов пиццерии", "время в стопе пиццерии"],
    "stop_minutes_sector":    ["время стопов секторов", "время в стопе секторов"],
    "stop_minutes_subsector": ["время стопов подсекторов", "время в стопе подсекторов"],
    "stop_count_unit":        ["кол-во стопов пиццерии", "количество стопов пиццерий"],
    "stop_count_sector":      ["кол-во стопов секторов", "количество стопов секторов"],
    "stop_count_subsector":   ["кол-во стопов подсекторов", "количество стопов подсекторов"],
}

PCT_FIELDS = ("lost_pct_total", "lost_pct_unit", "lost_pct_sector", "lost_pct_subsector")
RUB_FIELDS = ("lost_rub_total", "lost_rub_unit", "lost_rub_sector", "lost_rub_subsector")
DURATION_FIELDS = ("stop_minutes_unit", "stop_minutes_sector", "stop_minutes_subsector")
COUNT_FIELDS = ("stop_count_unit", "stop_count_sector", "stop_count_subsector")


def norm(s) -> str:
    return " ".join(str(s or "").lower().replace("\xa0", " ").split())


def map_headers(headers: list) -> tuple[dict, list]:
    """(индекс колонки -> наше имя поля, список нераспознанных заголовков)."""
    mapping, unknown = {}, []
    for i, h in enumerate(headers):
        h_norm = norm(h)
        if not h_norm:
            continue
        best_field, best_len = None, 0
        for field, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in h_norm and len(alias) > best_len:
                    best_field, best_len = field, len(alias)
        if best_field:
            mapping[i] = best_field
        else:
            unknown.append(str(h))
    return mapping, unknown


def to_number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace("%", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def to_minutes(value):
    """Время стопов приходит как "12:1:44" (Ч:М:С) — переводим в минуты."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        while len(nums) < 3:
            nums.append(0.0)
        return nums[0] * 60 + nums[1] + nums[2] / 60
    return to_number(text)


def to_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_table(path: str, sheet: str | None) -> tuple[list, list[list]]:
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            rows = [r for r in csv.reader(f, dialect)]
    # Заголовок — первая строка, в которой распозналась колонка «Пиццерия»: Superset
    # любит класть сверху название виджета и пустые строки.
    for i, row in enumerate(rows):
        mapping, _ = map_headers(row)
        if "unit_name" in mapping.values():
            return row, rows[i + 1:]
    raise SystemExit("Не нашёл строку заголовка с колонкой «Пиццерия» — проверьте файл/лист.")


def infer_period_ends(period_starts: set) -> dict:
    """Выгрузка даёт только начало периода. Шаг между соседними периодами и говорит,
    что это было — неделя или месяц. Один-единственный период трактовать нечем,
    тогда нужен --period-end."""
    starts = sorted(period_starts)
    if len(starts) < 2:
        return {}
    gaps = {(b - a).days for a, b in zip(starts, starts[1:])}
    ends = {}
    for i, start in enumerate(starts):
        if i + 1 < len(starts):
            ends[start] = starts[i + 1] - timedelta(days=1)
        else:
            # последний период — по типичному шагу
            ends[start] = start + timedelta(days=min(gaps) - 1)
    return ends


def derive_lost_rub(share_fraction, revenue):
    """Доля в дашборде считается от суммы «выручка + упущенная выручка», поэтому
    обратный ход — не share*revenue, а share*revenue/(1-share)."""
    if share_fraction is None or revenue is None or share_fraction >= 1:
        return None
    return share_fraction * revenue / (1 - share_fraction)


def parse_rows(path: str, sheet: str | None, period_start, period_end, exported_at) -> list[dict]:
    headers, body = read_table(path, sheet)
    mapping, unknown = map_headers(headers)
    if unknown:
        print(f"Нераспознанные колонки (игнорирую): {', '.join(unknown)}")

    raw_rows = []
    for row in body:
        rec = {field: (row[i] if i < len(row) else None) for i, field in mapping.items()}
        if not norm(rec.get("unit_name")):
            continue
        rec["_start"] = to_date(rec.get("period")) or period_start
        if rec["_start"] is None:
            raise SystemExit("Нет колонки периода — задайте --period-start/--period-end.")
        raw_rows.append(rec)
    if not raw_rows:
        raise SystemExit("В файле нет строк с пиццериями.")

    ends = {} if period_end else infer_period_ends({r["_start"] for r in raw_rows})
    if not ends and not period_end:
        raise SystemExit("В выгрузке один период — задайте --period-end (граница включительная).")

    # Доли приходят то долей единицы, то процентами — решаем по всему столбцу сразу,
    # чтобы одна маленькая неделя не увела масштаб.
    scale = {}
    for field in PCT_FIELDS:
        values = [v for v in (to_number(r.get(field)) for r in raw_rows) if v is not None]
        scale[field] = 100.0 if values and max(values) <= 1.0 else 1.0

    out = []
    for rec in raw_rows:
        item = {
            "period_start": rec["_start"],
            "period_end": period_end or ends[rec["_start"]],
            "unit_name": str(rec["unit_name"]).strip(),
            "exported_at": exported_at,
            "revenue_rub": to_number(rec.get("revenue_rub")),
        }
        for field in PCT_FIELDS:
            v = to_number(rec.get(field))
            item[field] = v * scale[field] if v is not None else None
        for field in RUB_FIELDS:
            item[field] = to_number(rec.get(field))
        for field in DURATION_FIELDS:
            item[field] = to_minutes(rec.get(field))
        for field in COUNT_FIELDS:
            v = to_number(rec.get(field))
            item[field] = int(v) if v is not None else None
        for pct_field, rub_field in zip(PCT_FIELDS, RUB_FIELDS):
            if item[rub_field] is None and item[pct_field] is not None:
                item[rub_field] = derive_lost_rub(item[pct_field] / 100, item["revenue_rub"])
        out.append(item)
    return out


COLUMNS = ("period_start", "period_end", "unit_name", "revenue_rub",
           *PCT_FIELDS, *RUB_FIELDS, *DURATION_FIELDS, *COUNT_FIELDS, "exported_at")


def upsert_rows(rows: list[dict]):
    conn = get_connection()
    cur = get_cursor(conn)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS[3:])
    psycopg2.extras.execute_values(
        cur,
        f"""
        INSERT INTO dodois_unit_sector_stops ({", ".join(COLUMNS)})
        VALUES %s
        ON CONFLICT (period_start, period_end, unit_name) DO UPDATE SET
            {updates}, loaded_at = now()
        """,
        [tuple(r[c] for c in COLUMNS) for r in rows],
        page_size=500,
    )
    conn.commit()
    conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="xlsx или csv выгрузка из Dodo IS")
    parser.add_argument("--sheet", default=None)
    parser.add_argument("--exported-at", required=True,
                        help="дата выгрузки из Dodo IS (YYYY-MM-DD) — цифра дозревает неделями")
    parser.add_argument("--period-start", default=None,
                        help="если в файле нет колонки периода (границы ВКЛЮЧИТЕЛЬНЫЕ)")
    parser.add_argument("--period-end", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = parse_rows(
        args.path, args.sheet,
        date.fromisoformat(args.period_start) if args.period_start else None,
        date.fromisoformat(args.period_end) if args.period_end else None,
        date.fromisoformat(args.exported_at),
    )
    for r in sorted(rows, key=lambda r: (r["period_start"], r["unit_name"])):
        print(f"  {r['period_start']}..{r['period_end']} | {r['unit_name']:<10} | "
              f"выручка {r['revenue_rub'] or 0:>11,.0f} | "
              f"пиццерия {r['lost_pct_unit'] or 0:>6.3f}% / {r['lost_rub_unit'] or 0:>9,.0f} ₽ | "
              f"сектор {r['lost_pct_sector'] or 0:>6.3f}% / {r['lost_rub_sector'] or 0:>9,.0f} ₽")
    if args.dry_run:
        print(f"\n--dry-run: {len(rows)} строк не записано.")
        return
    upsert_rows(rows)
    print(f"\n💾 Загружено строк: {len(rows)}")


if __name__ == "__main__":
    main()
