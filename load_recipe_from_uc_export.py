# Загружает карту рецептур (продукт -> сырьё) из выгрузки Dodo IS «UC-<Город>-<дата>.xlsx»,
# лист «Состав продуктов», в таблицу product_recipe с source='uc-export'.
#
# Зачем ещё один загрузчик, когда есть parse_ttk_recipes.py: тот читает ТТК-файлы, где
# названия набраны людьми, и потому вынужден сопоставлять их с нашими справочниками
# через fuzzy-match. В шапке parse_ttk_recipes.py описано, чем это кончилось: WRatio
# держит «пол» ~85.5 для НЕсвязанных строк с общим служебным словом («тесто для пиццы»
# <-> «коробка для закусок мини»), пороги пришлось задрать до 95 и потерять 29% продуктов.
#
# Здесь fuzzy не нужен вовсе. Выгрузка UC приходит из самой Dodo IS, поэтому имена в ней
# совпадают с material_classification.material_name и product_classification.product_name
# буквально — достаточно нормализации регистра, ё/е и пробелов. Весь класс ошибок
# ложного сопоставления просто не возникает.
#
# Охват на выгрузке от 14.09.2026: 20 741 пара, 498 ингредиентов, 3 466 продуктов —
# против 1 490 пар и ~100 ингредиентов в карте до этого. Из 60 ингредиентов, которые
# вставали в стопы и отсутствовали в карте, выгрузка закрывает 59 (см. issue #15, #6).
#
# По умолчанию — сухой прогон: считает и показывает образец, в базу не пишет.
# Запись включается флагом --apply.
import argparse
import re

import pandas as pd
import psycopg2.extras

from db import get_connection, get_cursor

SHEET = "Состав продуктов"
SOURCE = "uc-export"


# Латиница, неотличимая на вид от кириллицы. В выгрузке UC суффикс толстого теста
# набран кириллической «Т» («Флотская 30 Т»), а в product_classification — латинской
# «T» («Флотская 30 T»). Строки выглядят одинаково, но не равны, и из-за этого
# 14.09.2026 из карты выпали ВСЕ варианты теста «Т»: на одном стопе «Соуса Фирменного»
# мы видели 7 продуктов вместо 14 у Dodo IS. Сворачиваем обе стороны к кириллице —
# операция симметричная, склеить может только строки, отличающиеся ровно омоглифами.
HOMOGLYPHS = str.maketrans({
    "a": "а", "b": "ь", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
})


def norm(s) -> str:
    """Нормализация имени: регистр, ё->е, омоглифы, лишние пробелы."""
    v = str(s).lower().replace("ё", "е").translate(HOMOGLYPHS)
    return re.sub(r"\s+", " ", v).strip()


def to_number(v):
    """«240,00» -> 240.0. Прочерки и пустые -> None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if s in ("", "-", "—", "∞"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse(path: str) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=SHEET, header=0)
    raw.columns = [str(c).strip() for c in raw.columns]
    # Продукт стоит только в первой строке своего блока — растягиваем вниз.
    raw["Продукт"] = raw["Продукт"].ffill()
    comp = raw[raw["Тип сырья"].notna()].copy()
    comp["product_name"] = comp["Продукт"].astype(str).str.strip()
    comp["material_name"] = comp["Тип сырья"].astype(str).str.strip()
    comp["grams"] = comp["Итого (г,мл,шт,м)"].map(to_number)
    return comp[["product_name", "material_name", "grams"]]


def resolve(comp: pd.DataFrame, cur):
    """Имя -> uuid. Продукт может иметь ОДНОИМЁННЫХ двойников с разными uuid
    (например «Флотская 35» есть и в «Обязательном ассортименте», и в «Распродаже»).
    Раньше словарь оставлял произвольного из них, и связь уходила на тот SKU, который
    метрика отфильтровывает: на стопе «Соуса Фирменного» так терялись «Флотская 35»
    и «Додо (фирменный) 35». Поэтому связь заводим на КАЖДЫЙ одноимённый uuid —
    рецептура у них общая, а лишнее отсечёт фильтр ассортимента у потребителя."""
    cur.execute("SELECT material_uuid, material_name FROM material_classification")
    mmap = {}
    for r in cur.fetchall():
        mmap.setdefault(norm(r["material_name"]), r["material_uuid"])

    cur.execute("SELECT product_uuid, product_name FROM product_classification")
    pmap: dict[str, list[str]] = {}
    for r in cur.fetchall():
        pmap.setdefault(norm(r["product_name"]), []).append(r["product_uuid"])

    comp = comp.copy()
    comp["material_uuid"] = comp["material_name"].map(lambda s: mmap.get(norm(s)))
    comp["product_uuids"] = comp["product_name"].map(lambda s: pmap.get(norm(s)))
    comp = comp.explode("product_uuids").rename(columns={"product_uuids": "product_uuid"})
    return comp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="путь к UC-<Город>-<дата>.xlsx")
    ap.add_argument("--apply", action="store_true", help="писать в БД (без флага — сухой прогон)")
    ap.add_argument("--sample", type=int, default=25, help="сколько строк показать для глазной проверки")
    args = ap.parse_args()

    comp = parse(args.file)
    print(f"Строк состава в выгрузке: {len(comp)}")

    conn = get_connection()
    cur = get_cursor(conn)
    comp = resolve(comp, cur)

    ok = comp.dropna(subset=["material_uuid", "product_uuid"])
    print(f"  сырьё опознано:   {comp['material_uuid'].notna().sum()} ({comp['material_uuid'].notna().mean()*100:.0f}%)")
    print(f"  продукт опознан:  {comp['product_uuid'].notna().sum()} ({comp['product_uuid'].notna().mean()*100:.0f}%)")
    print(f"  обе стороны:      {len(ok)} строк")

    # Одна пара может встретиться несколько раз (разные порции одного продукта) —
    # схлопываем, граммовки складываем.
    pairs = (ok.groupby(["product_uuid", "material_uuid"], as_index=False)
               .agg(grams=("grams", "sum"),
                    product_name=("product_name", "first"),
                    material_name=("material_name", "first")))
    print(f"  уникальных пар:   {len(pairs)} "
          f"({pairs['material_uuid'].nunique()} ингредиентов, {pairs['product_uuid'].nunique()} продуктов)")

    cur.execute("SELECT product_uuid, material_uuid FROM product_recipe")
    have = {(r["product_uuid"], r["material_uuid"]) for r in cur.fetchall()}
    new = pairs[[(p, m) not in have for p, m in zip(pairs.product_uuid, pairs.material_uuid)]]
    print(f"  из них новых:     {len(new)} (уже в карте: {len(pairs) - len(new)})")

    # Имена, которые не опознались, — самое полезное для следующего круга.
    bad_mat = sorted({m for m, u in zip(comp.material_name, comp.material_uuid) if pd.isna(u)})
    print(f"\nНе опознано видов сырья: {len(bad_mat)}")
    for m in bad_mat[:15]:
        print(f"    {m}")

    print(f"\nОбразец новых связей ({min(args.sample, len(new))} из {len(new)}):")
    for r in new.head(args.sample).itertuples():
        g = "—" if pd.isna(r.grams) else f"{r.grams:g}"
        print(f"    {r.material_name[:34]:34} -> {r.product_name[:44]:44} {g:>8}")

    if not args.apply:
        print("\nСухой прогон: в базу ничего не записано. Повторить с --apply.")
        conn.close()
        return

    # Существующие пары не трогаем: строки source='ttk' прошли ручной аудит 231 названия
    # (см. parse_ttk_recipes.py), перезаписывать их выгрузкой смысла нет — потребителю
    # метрики нужна сама связь, а не граммовки.
    rows = [(r.product_uuid, r.material_uuid, r.grams if not pd.isna(r.grams) else None, SOURCE)
            for r in new.itertuples()]
    psycopg2.extras.execute_values(cur, """
        INSERT INTO product_recipe (product_uuid, material_uuid, netto_grams, source)
        VALUES %s ON CONFLICT (product_uuid, material_uuid) DO NOTHING
    """, rows, page_size=500)
    conn.commit()
    cur.execute("SELECT source, COUNT(*) n FROM product_recipe GROUP BY 1 ORDER BY 2 DESC")
    print("\nКарта рецептур после загрузки:")
    for r in cur.fetchall():
        print(f"    {r['source']:>14}: {r['n']}")
    conn.close()


if __name__ == "__main__":
    main()
