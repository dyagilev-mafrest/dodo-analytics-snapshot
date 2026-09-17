# CSV -> PostgreSQL (Supabase): revenue_forecast_scenario_monthly
#
# Сценарный прогноз выручки из артефакта «Прогноз выручки Якутск Dodo Pizza
# 2026-2027». Ряд лежит файлом в репозитории (data/revenue_forecast_*.csv), а не
# константами в коде: числа сняты из документа руками, и в диффе они должны
# читаться как данные, которые можно сверить, а не как правка программы.
# Ровно на этом обожглись с планом выручки — в коде год назад лежал снимок не с
# того листа, и заметить это в Python было нечем.
#
# Usage:
#   python load_revenue_forecast_scenario.py data/revenue_forecast_2026_base.csv --scenario base
#   ... --dry-run
import csv
import io
import sys
from datetime import date

from db import get_connection, get_cursor

SOURCE_NOTE = (
    "Артефакт «Прогноз выручки Якутск Dodo Pizza 2026-2027», составлен 25.06.2026, "
    "открытия актуализированы 09.09.2026, ряд снят 16.09.2026"
)


def load_rows(path: str, scenario: str) -> list[dict]:
    out: list[dict] = []
    with io.open(path, encoding="utf-8", newline="") as fh:
        # Комментарии в шапке файла держат происхождение ряда рядом с числами.
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    for row in csv.DictReader(lines):
        month = (row.get("month") or "").strip()
        raw = (row.get("revenue_mln") or "").strip()
        if not month or not raw:
            continue
        year, mon = month.split("-")
        out.append({
            "month": date(int(year), int(mon), 1),
            "scenario": scenario,
            # В файле миллионы — так ряд читается глазами; в базе рубли, как у
            # выручки и плана, чтобы дашборд ничего не домножал.
            "revenue_rub": round(float(raw) * 1_000_000),
        })
    if not out:
        raise ValueError(f"В файле {path} не нашлось ни одной строки с месяцем и выручкой.")
    return out


def upsert(rows: list[dict]):
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO revenue_forecast_scenario_monthly (month, scenario, revenue_rub, source_note)
        VALUES %s
        ON CONFLICT (month, scenario) DO UPDATE
           SET revenue_rub = EXCLUDED.revenue_rub,
               source_note = EXCLUDED.source_note,
               loaded_at = now()
    """, [(r["month"], r["scenario"], r["revenue_rub"], SOURCE_NOTE) for r in rows], page_size=200)
    conn.commit()
    conn.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scenario = "base"
    for i, a in enumerate(sys.argv):
        if a == "--scenario" and i + 1 < len(sys.argv):
            scenario = sys.argv[i + 1]
    if not args:
        print("Usage: python load_revenue_forecast_scenario.py <файл.csv> [--scenario base]")
        sys.exit(1)

    rows = load_rows(args[0], scenario)
    by_year: dict[int, float] = {}
    for r in rows:
        by_year[r["month"].year] = by_year.get(r["month"].year, 0) + r["revenue_rub"]
    print(f"Сценарий {scenario}: {len(rows)} месяцев, {rows[0]['month']} - {rows[-1]['month']}")
    for year, total in sorted(by_year.items()):
        print(f"  {year}: {total / 1_000_000:,.1f} млн")

    if "--dry-run" in sys.argv:
        print("--dry-run: в БД не пишем.")
        return
    upsert(rows)
    print(f"Загружено {len(rows)} строк в revenue_forecast_scenario_monthly.")


if __name__ == "__main__":
    main()
