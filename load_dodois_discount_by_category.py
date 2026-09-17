# Ручные экспорты Dodo IS Superset -> dodois_discount_by_category_monthly.
#
# Два дашборда семейства «Discount Analytics» отдают один разрез — месяц × тип ×
# подкатегория акции, — но разные меры:
#   share — «Share of Orders with discount by Type and subcategory in dynamic»,
#           доля ЗАКАЗОВ со скидкой этого вида (0-1);
#   rub   — «Discount by DodoIs category in dynamic», сумма скидки в рублях.
# Колонки в обоих идут парой «Тип, Подкатегория»: «Другое, Локальные», «CVM,
# Флеш» и так далее. Файл может содержать одну подкатегорию — руководство
# фильтрует дашборд перед выгрузкой.
#
# МЕРЫ НЕ ВЫВОДЯТСЯ ОДНА ИЗ ДРУГОЙ. За август 2026 «Другое, Локальные» — это
# 0,42% заказов и 274 277 ₽. Долю от выручки считает дашборд: делит рубли на
# нашу выручку без дисконта, ту же базу, что у дисконта общего.
#
# Не API-загрузка, расписания нет — запускать руками при новой выгрузке.
#
# Usage:
#   python load_dodois_discount_by_category.py <файл.csv> --measure share
#   python load_dodois_discount_by_category.py <файл.csv> --measure rub
#   ... --dry-run  — разобрать и показать, в БД не писать
import csv
import io
import sys
from datetime import date, datetime

from db import get_connection, get_cursor

# Superset отдаёт CSV с точкой с запятой и BOM, дробь — с точкой.
DELIMITER = ";"
PERIOD_HEADER = "Период"


def parse_header(cell: str) -> tuple[str, str]:
    """«Другое, Локальные» -> ("Другое", "Локальные").

    Запятая в заголовке — единственный разделитель типа и подкатегории. Если её
    нет, вся метка считается типом, а подкатегория остаётся пустой: молча
    склеивать в одну строку хуже, чем показать метку как есть.
    """
    head, sep, tail = cell.partition(",")
    return (head.strip(), tail.strip()) if sep else (cell.strip(), "")


def parse_month(raw: str) -> date:
    return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date().replace(day=1)


def load_rows(path: str) -> list[dict]:
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=DELIMITER))
    if not rows:
        raise ValueError(f"Файл {path} пуст.")

    header = rows[0]
    if header[0].strip() != PERIOD_HEADER:
        raise ValueError(
            f"Первая колонка называется {header[0]!r}, а ожидается {PERIOD_HEADER!r} — "
            f"это другая выгрузка или у дашборда сменилась разметка."
        )

    out: list[dict] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        month = parse_month(row[0])
        for col, cell in enumerate(row[1:], start=1):
            if col >= len(header):
                break
            raw = (cell or "").strip()
            # Пустая клетка — у этой подкатегории в месяце не было заказов
            # вовсе. Это не ноль: ноль означал бы «акция была, скидок нет».
            if not raw:
                continue
            discount_type, subcategory = parse_header(header[col])
            out.append({
                "month": month,
                "discount_type": discount_type,
                "subcategory": subcategory,
                "value": float(raw),
            })
    return out


# Мера -> колонка. Обновляется только своя колонка: два экспорта приходят
# порознь, и загрузка рублей не должна стирать уже загруженные доли.
COLUMNS = {"share": "order_share", "rub": "discount_rub"}


def upsert(rows: list[dict], measure: str):
    import psycopg2.extras
    column = COLUMNS[measure]
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, f"""
        INSERT INTO dodois_discount_by_category_monthly (month, discount_type, subcategory, {column})
        VALUES %s
        ON CONFLICT (month, discount_type, subcategory) DO UPDATE
           SET {column} = EXCLUDED.{column}, loaded_at = now()
    """, [(r["month"], r["discount_type"], r["subcategory"], r["value"]) for r in rows],
        page_size=500)
    conn.commit()
    conn.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    measure = None
    for i, a in enumerate(sys.argv):
        if a == "--measure" and i + 1 < len(sys.argv):
            measure = sys.argv[i + 1]
    if not args or measure not in COLUMNS:
        print("Usage: python load_dodois_discount_by_category.py <файл.csv> --measure share|rub")
        sys.exit(1)
    dry_run = "--dry-run" in sys.argv

    rows = load_rows(args[0])
    months = sorted({r["month"] for r in rows})
    subs = sorted({f'{r["discount_type"]}, {r["subcategory"]}'.rstrip(", ") for r in rows})
    print(f"{len(rows)} значений, мера {measure}: {len(months)} месяцев, до {len(subs)} подкатегорий")
    print(f"  месяцы: {months[0]} — {months[-1]}")
    print(f"  подкатегории: {', '.join(subs)}")

    local = [r for r in rows if r["subcategory"] == "Локальные"]
    if local:
        print("\n  Локальные акции:")
        for r in sorted(local, key=lambda r: r["month"]):
            shown = f"{r['value'] * 100:.2f}%" if measure == "share" else f"{r['value']:,.0f} руб."
            print(f"    {r['month']}  {shown}")
    else:
        # «ВНИМАНИЕ» словом, а не значком: консоль Windows здесь в cp1251,
        # и предупреждение падало бы ровно тогда, когда его надо показать.
        print("\n  ВНИМАНИЕ: подкатегории «Локальные» в выгрузке нет — проверьте фильтры дашборда.")

    if dry_run:
        print("\n--dry-run: в БД не пишем.")
        return
    upsert(rows, measure)
    print(f"\nЗагружено {len(rows)} строк ({COLUMNS[measure]}) в dodois_discount_by_category_monthly.")


if __name__ == "__main__":
    main()
