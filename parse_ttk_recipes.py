# Парсит официальные ТТК-файлы (xlsx с одним листом на продукт-вариант + лист
# "Реестр" с маппингом код листа -> название блюда) в таблицу product_recipe
# (product_uuid -> material_uuid). Ручной запуск, файлы не в репозитории — передаются
# аргументами; пере-запускать при получении новой выгрузки ТТК (см. issue #6,
# память project_dodo_stops_products_ingredients.md).
#
# Сопоставление названий (ТТК -> наши uuid-справочники) — fuzzy-match rapidfuzz WRatio,
# после нормализации (lowercase, ё->е, без кавычек/лишних пробелов).
#
# ⚠️ ВАЖНО (найдено 2026-08-31, см. issue #6): WRatio даёт систематический "пол" score
# ~85.5 для совершенно НЕ связанных строк, если они делят одно общее служебное/категорийное
# слово ("соус", "концентрат", "для" и т.п.) — пример: "тесто для пиццы" сопоставлялось
# с "коробка для закусок мини" на 85.5, а разные "Пицца Х" из книги "Евразия"
# (зарубежный ассортимент, не наш) массово схлопывались в "Пицца Пепперони 35" на 85.5.
# Порог 85 (использовавшийся в первой версии этого скрипта) НЕЛЬЗЯ доверять без ручной
# проверки — он даёт кучу ложных срабатываний. Поэтому:
#   - продукты -> порог 95 (PRODUCT_MATCH_THRESHOLD), БЕЗ ручных исключений ниже порога —
#     решение пользователя: лучше потерять охват (~427/1455, ~29%), чем рисковать
#     склейкой разных SKU (целая пицца/кусочки, halal/обычная и т.п.)
#   - ингредиенты -> порог 95 (INGREDIENT_MATCH_THRESHOLD) + курированный список
#     CONFIRMED_INGREDIENT_MATCHES для проверенных вручную случаев 85-95 (словоформы/
#     порядок слов), см. память project_dodo_stops_products_ingredients.md
#   - тесто ("тесто для пиццы" — родовое название без размера в ТТК, а в справочнике
#     сырья тесто разбито по размеру: Тесто 20/25/30/35) — отдельная логика: размер
#     берётся из названия блюда в Реестре (regex по "NNсм"), а не из fuzzy-match.
# Непойманные имена пропускаются (печатаются в лог) — без дальнейшей калибровки.
import argparse
import re
import sys
from collections import defaultdict

import openpyxl
import psycopg2.extras
from rapidfuzz import fuzz, process

from db import get_connection, get_cursor
PRODUCT_MATCH_THRESHOLD = 95
INGREDIENT_MATCH_THRESHOLD = 95
NON_PRODUCT_SHEETS = {"Инфо", "Инструкция", "Реестр"}

# Ингредиенты, где ТТК-название не совпадает по словоформе/порядку слов с нашим
# справочником, но это точно тот же материал — проверено вручную 2026-08-31 построчным
# аудитом всех 231 уникальных названий (см. audit в issue #6). Каждая запись:
# нормализованное ТТК-имя -> строка для повторного поиска (обычно точное или почти
# точное имя в material_classification/write-offs). НЕ добавлять новые записи без
# проверки, что целевая строка реально существует и это тот же продукт — именно
# бездумное доверие высокому WRatio-score без такой проверки и было причиной бага.
CONFIRMED_INGREDIENT_MATCHES = {
    "бекон слайсовый": "бекон слайсами",
    "огурцы консервированные": "огурцы маринован.",
    "огурцы свежие": "огурец свежий",
    "сыр чеддар+пармезан": "смесь сыров чеддер и пармезан",
    "лук красный свежий": "лук красный",
    "колбаса пепперони с/к": "пепперони",
    "колбаса чоризо с/к": "чоризо",
    "колбаса чоризо (халяль)": "чоризо",
    "колбаса пепперони (халяль)": "пепперони",
    "основа для пиццы (парбейк/корж для пиццы)": "корж для пиццы",
    "брынза мягкая": "брынза",
    "ветчина оригинальная": "ветчина",
    "ветчина свинина": "ветчина",
    "соус терияки (новый)": "соус терияки",
    "брусника св/зам": "брусника",
    "соус сырный чеддер": "соус сырный",
    "мука пшеничная высшего сорта": "мука",
    "вода питьевая": "вода",
    "гренки для салата": "гренки",
    "соус цезарь": "соус цезарь порционный",
    "маслины резаные консервированные": "маслины",
    "сырники замороженные 70г": "сырники",
    "наггетсы": "наггетсы куриные",
    'картофельные дольки "крисперс"': "картофельные дольки",
    'котлеты картофельные "хашбрауны"': "хашбраун",
    "картофельные дольки со специями, с кожурой": "картофельные дольки",
    "пита пшеничная": "пита",
    "паста отварная": "паста",
    "мини-тортилья «тортилья пшеничная» 8": "мини-тортилья",
    "молоко ультрапастеризованное 3,2%": "молоко",
    "молоко ультрапастеризованное 3,2% (ст)": "молоко",
    "молоко ультрапастеризованное 3,2% (кс)": "молоко",
    "мороженое пломбир": "мороженое",
    "наполнитель шоколадный соус (ст)": "шоколадный соус",
    "додстер острый, п/ф": "п/ф додстер острый",
    "додстер с ветчиной, п/ф": "п/ф додстер с ветчиной",
    "додстер супермясной, п/ф": "п/ф додстер супермясной",
    "додстер классический, п/ф": "п/ф додстер классический",
    "слоеное тесто (мини)": "слоеное тесто мини",
    "сахар-песок": "сахар песок",
    "перец халапеньо консервированный": "халапеньо консерв.",
    "ананасы кусочки консервированные": "ананасы консерв",
    'куриные крылья "барбекю"': "крылья барбекю",
    "пирожное шоколадное фондан": "фондан шоколадный",
    "масло подсолнечное рафинированное, дезодорированное, вымороженное": "масло растительное",
    "креветки белоногие очищенные в панировке": "креветки в панировке",
    # найдено в stock_items_catalog 2026-08-31 (реверс-инжиниринг 2mozg-oss, issue #6) —
    # тот же класс проблемы, WRatio не дотягивает до 95 из-за лишних слов в ТТК-названии
    "сыр полутвердый чеддер палочки замороженные": "сыр чеддер палочки",
    "балык": "колбаса балыковая",
    "чизибайтс с сыром моцарелла": "чизибайтсы",
    "соус ранч": "соус ранч порционный",
    "цыпленок филе грудки запеченное": "цыпленок филе",
    "перец зеленый свежий": "перец свежий",
    'вода газированная "бонаква': "вода бонааква газ 1л",
    'вода негазированная "бонаква': "вода бонааква негаз 1л",
}

# "тесто для пиццы"/аналоги — родовое название в ТТК без размера; в справочнике сырья
# тесто разбито по размеру (Тесто 20/25/30/35). Размер берётся из названия блюда
# (Реестр), не из fuzzy-match над названием ингредиента.
DOUGH_GENERIC_NAMES = {
    "тесто для пиццы",
    "полуфабрикат высокой степени готовности. основа для пиццы",
}
DOUGH_SIZE_RE = re.compile(r"(\d{2})\s*см", re.IGNORECASE)

# Продукты в зоне score 90-95 (см. предупреждение выше) в основном легитимны — Dodo
# хранит короткие канонические имена ("чоризо" для "Пицца Чоризо с овощами"), но
# несколько реальных ошибок нашлись при ручном аудите 2026-08-31: WRatio склеивал
# РАЗНЫЕ SKU одного блюда — целую пиццу с вариантом "кусочки", обычную с halal-версией
# ("без свинины"). Отклоняем совпадение в этой зоне, если набор признаков не совпадает
# между ТТК-названием и найденным кандидатом (даже при высоком score) — только >=95
# принимается безусловно.
PRODUCT_VARIANT_MARKERS = [
    ("halal", re.compile(r"халял|без свинины", re.IGNORECASE)),
    ("piece", re.compile(r"кусочк", re.IGNORECASE)),
    ("heated_cabinet", re.compile(r"тепловой шкаф", re.IGNORECASE)),
    ("pizzetta", re.compile(r"пиццетта", re.IGNORECASE)),  # отдельная линейка от обычной пиццы
]

# Ещё один найденный при ручном аудите класс ложных срабатываний: очень короткие
# (случайные, напр. промо-пицца "СКА" — реальный, но совсем другой продукт) названия
# в product_classification, с которыми WRatio совпадает почти с чем угодно за счёт
# небольшой абсолютной длины строки. "додо"/"чоризо" — легитимные короткие канонические
# имена (4+ букв); 3-буквенные и короче — отбрасываем безусловно.
MIN_CANDIDATE_LEN = 4


def product_variant_markers(s):
    return frozenset(tag for tag, pat in PRODUCT_VARIANT_MARKERS if pat.search(s or ""))


# Ещё один найденный класс ошибок в зоне 90-95: candidate теряет вкус/начинку из
# query ("Шоколадный Айс латте" -> "латте", "Домашний лимонад бабл-личи" -> "домашний
# лимонад") — WRatio даёт высокий score за счёт совпадения родовой части, отбрасывая
# отличительное слово. Требуем, чтобы каждое содержательное слово query (кроме общих
# категорийных/размерных модификаторов) встречалось (по 5-буквенному префиксу — грубая
# компенсация словоформ) в candidate.
CONTENT_STOPWORDS = {"пицца", "большой", "маленький", "лед", "лёд", "станция", "зап", "новые"}
# "200мл"/"0,2" и т.п. — объём, уже отдельно сверяется через extract_volume_l();
# как "содержательное слово" не нужен и ломает сверку буквальным текстом ("200мл"
# не совпадает по написанию с "0.2" для того же объёма) — нашёл 2026-08-31, когда
# из-за этого не находились базовые "Кофе Капучино/Латте/Американо 0.2/0.3/0.4"
# (Обязательный ассортимент, реальные продажи) для сопоставления с "Кофе зерно".
VOLUME_TOKEN_RE = re.compile(r"^\d{1,4}мл$|^\d(,\d{1,2})?$")


def content_words(s):
    tokens = re.split(r"[^a-zа-я0-9]+", s.lower())
    return [t for t in tokens if len(t) >= 3 and t not in CONTENT_STOPWORDS and not t.isdigit() and not VOLUME_TOKEN_RE.match(t)]


def covers_content_words(query, candidate):
    return all(w[:5] in candidate for w in content_words(query))


# Найдено 2026-08-31 при разборе, почему "ключевые ингредиенты" не сходятся с Dodo IS
# (issue #6): для напитков (кофе и т.п.) в product_classification — БУКВАЛЬНО СОТНИ
# почти дублирующих строк на одно название ("Американо" — 56 строк: разные объёмы,
# "(сотрудники)", "(не добавлять в меню)", "Specialty", "на доставку" и т.п.), и
# единственный "лучший" fuzzy-match почти всегда промахивается мимо нужного варианта
# или вообще не проходит порог. При этом нужный вариант (напр. "Кофе Флэт уайт 0.2",
# статус "Обязательный ассортимент") реально существует в справочнике. Решение —
# та же идея, что и для размера теста: извлекаем ОБЪЁМ (мл/л) из названия и требуем
# его совпадения как дополнительный сигнал, плюс исключаем заведомо служебные SKU.
EXCLUDED_CANDIDATE_RE = re.compile(r"сотрудник|не добавлять|specialty|на доставку", re.IGNORECASE)
VOLUME_ML_RE = re.compile(r"(\d{2,4})\s*мл")
VOLUME_FRAC_RE = re.compile(r"(?<!\d)(\d)[.,](\d{1,2})(?!\d)")


def extract_volume_l(s):
    if not s:
        return None
    m = VOLUME_ML_RE.search(s)
    if m:
        return round(int(m.group(1)) / 1000, 2)
    m = VOLUME_FRAC_RE.search(s)
    if m:
        return round(float(f"{m.group(1)}.{m.group(2)}"), 2)
    return None


def norm(s):
    if not s:
        return ""
    s = s.strip().strip('"').strip()
    s = re.sub(r"\s+", " ", s)
    s = s.lower().replace("ё", "е")
    return s


def parse_reestr(ws):
    """Код листа (вариант размер/тесто/борт) -> человекочитаемое название блюда."""
    code_to_name = {}
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        name_cell = row[1] if len(row) > 1 else None
        if not isinstance(name_cell, str) or not name_cell.strip():
            continue
        name = name_cell.strip()
        for c in row[2:11]:
            if isinstance(c, str) and c.strip() and c.strip() not in ("—", "-"):
                code_to_name[c.strip()] = name
            elif isinstance(c, (int, float)):
                code_to_name[str(c)] = name
    return code_to_name


def find_recipe_rows(ws):
    """Ищем строку '3. РЕЦЕПТУРА' -> (title, ингредиенты (имя, брутто, нетто) до 'Выход блюда:').
    title — строка листа с полным названием+размером (например '"ПИЦЦА ВЕТЧИНА И ГРИБЫ"
    25СМ ТОНКОЕ ТЕСТО'), берётся из строки сразу после 'ТЕХНИКО-ТЕХНОЛОГИЧЕСКАЯ КАРТА №' —
    в отличие от короткого reestr_name (из листа "Реестр"), она содержит размер пиццы,
    нужный для правильного выбора теста (см. DOUGH_SIZE_RE)."""
    rows = list(ws.iter_rows(values_only=True))
    start = None
    title = None
    for i, row in enumerate(rows):
        cell0 = row[0] if row else None
        if isinstance(cell0, str) and "ТЕХНИКО-ТЕХНОЛОГИЧЕСКАЯ КАРТА" in cell0.upper() and title is None:
            for r2 in rows[i + 1:i + 4]:
                c = r2[0] if r2 else None
                if isinstance(c, str) and c.strip():
                    title = c.strip()
                    break
        if isinstance(cell0, str) and "РЕЦЕПТУРА" in cell0.upper():
            start = i
            break
    if start is None:
        return title, []

    ingredients = []
    for row in rows[start + 1:]:
        cell0 = row[0] if row else None
        cell1 = row[1] if len(row) > 1 else None
        if isinstance(cell0, str) and "ВЫХОД БЛЮДА" in cell0.upper():
            break
        if isinstance(cell1, str) and cell1.strip():
            name = cell1.strip()
            if name.lower() == "наименование сырья и продуктов":
                continue
            brutto = row[3] if len(row) > 3 else None
            netto = row[4] if len(row) > 4 else None
            netto = netto if isinstance(netto, (int, float)) else None
            ingredients.append((name, netto))
        if len(ingredients) > 60:  # safety cap
            break
    return title, ingredients


def parse_book(path):
    """-> [{"reestr_name": str, "title": str|None, "ingredients": [(name, netto), ...]}, ...]"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    reestr_map = parse_reestr(wb["Реестр"]) if "Реестр" in wb.sheetnames else {}
    recipes = []
    for sheet_name in wb.sheetnames:
        if sheet_name in NON_PRODUCT_SHEETS or sheet_name not in reestr_map:
            continue
        title, ingredients = find_recipe_rows(wb[sheet_name])
        if ingredients:
            recipes.append({"reestr_name": reestr_map[sheet_name], "title": title, "ingredients": ingredients})
    wb.close()
    return recipes


def load_stock_items_catalog(cur):
    """id -> name из stock_items_catalog (см. migrations/051_stock_items_catalog.sql,
    load_stock_items_catalog.py) — полный справочник сырья/товаров из Dodo IS Accounting
    API (GET accounting/stock-items), 1466 позиций. Заменяет прежний подход через
    write-offs (250 позиций, только то, что реально когда-то списывали) — этот
    справочник полнее и не требует медленного постраничного обхода списаний за год.
    Ограничиваем категориями Ingredient/SemiFinishedProduct — остальные (FinishedProduct/
    Packing/Consumables/Inventory) не нужны для сопоставления ингредиентов рецептуры и
    только повышают риск случайных fuzzy-совпадений."""
    cur.execute(
        "SELECT stock_item_id, name FROM stock_items_catalog WHERE category_name IN ('Ingredient', 'SemiFinishedProduct')"
    )
    return {row["stock_item_id"]: row["name"] for row in cur.fetchall()}


def build_match_index(names_to_uuid: dict):
    """names_to_uuid: normalized_name -> uuid. Возвращает список нормализованных имён
    для rapidfuzz + сам словарь для обратного маппинга."""
    return list(names_to_uuid.keys())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pizza_xlsx")
    ap.add_argument("snacks_xlsx")
    ap.add_argument("drinks_xlsx")
    ap.add_argument("--dry-run", action="store_true", help="только посчитать статистику, не писать в БД")
    args = ap.parse_args()

    conn = get_connection()
    cur = get_cursor(conn)

    print("Читаю product_classification / material_classification...")
    cur.execute("SELECT product_uuid, product_name, meta_product_name, classification FROM product_classification")
    products_db = cur.fetchall()
    has_classification = {row["product_uuid"] for row in products_db if row["classification"]}
    cur.execute("SELECT material_uuid, material_name FROM material_classification")
    materials_db = cur.fetchall()

    product_names = {}
    for row in products_db:
        for key in ("product_name", "meta_product_name"):
            n = norm(row[key])
            if n:
                product_names.setdefault(n, row["product_uuid"])

    material_names = {}
    for row in materials_db:
        n = norm(row["material_name"])
        if n:
            material_names.setdefault(n, row["material_uuid"])

    stock_items = load_stock_items_catalog(cur)
    print(f"stock_items_catalog (Ingredient/SemiFinishedProduct): {len(stock_items)} позиций")
    for sid, name in stock_items.items():
        n = norm(name)
        if n:
            material_names.setdefault(n, sid)

    product_name_list = build_match_index(product_names)
    material_name_list = build_match_index(material_names)

    # Индекс по объёму для volume-aware rescue (см. ниже): process.extract с limit=N
    # не находит правильную цель, если у неё низкий WRatio (пунктуация/нотация объёма
    # снижают score) и сотни структурно похожих кандидатов с более высоким score её
    # вытесняют из top-N — нашёл 2026-08-31 на "Кофе Капучино 0.2" (Обязательный
    # ассортимент, реальные продажи), которая даже в top-100 не попадала. Вместо
    # ранжирования по score сначала фильтруем ВЕСЬ список по точному совпадению объёма.
    product_names_by_volume = defaultdict(list)
    for cand in product_name_list:
        vol = extract_volume_l(cand)
        if vol is not None:
            product_names_by_volume[vol].append(cand)

    print("\nПарсю ТТК-книги...")
    all_recipes = []
    for path in (args.pizza_xlsx, args.snacks_xlsx, args.drinks_xlsx):
        recipes = parse_book(path)
        print(f"  {path}: {len(recipes)} рецептов")
        all_recipes.extend(recipes)
    print(f"Всего рецептов: {len(all_recipes)}")

    rows = {}  # (product_uuid, material_uuid) -> netto_grams
    unmatched_products = []
    unmatched_ingredients = defaultdict(int)

    for recipe in all_recipes:
        query = norm(recipe["reestr_name"])
        result = process.extractOne(query, product_name_list, scorer=fuzz.WRatio)
        accepted = False
        matched_name = None
        if result:
            candidate, score, _ = result
            if len(candidate) >= MIN_CANDIDATE_LEN:
                if score >= PRODUCT_MATCH_THRESHOLD:
                    accepted, matched_name = True, candidate
                elif (score >= 90
                      and product_variant_markers(query) == product_variant_markers(candidate)
                      and covers_content_words(query, candidate)):
                    accepted, matched_name = True, candidate

        if not accepted:
            # Volume-aware rescue (см. комментарий у extract_volume_l/product_names_by_volume):
            # напитки в product_classification часто имеют десятки почти дублирующих строк
            # (объём/служебные варианты) — единственный "лучший" fuzzy-match мимо, а
            # process.extract(limit=N) даже не покажет правильную цель среди top-N, если у
            # неё низкий WRatio (пунктуация/нотация объёма) и полно похожих кандидатов с
            # более высоким score. Поэтому фильтруем по точному объёму СНАЧАЛА, из всего
            # списка, а не ранжируем top-N по score первым.
            query_volume = extract_volume_l(recipe["title"] or recipe["reestr_name"])
            if query_volume is not None:
                query_words = set(content_words(query))
                best = None  # (cand, extra_words_count, score) — меньше extra_words и выше score лучше
                for cand in product_names_by_volume.get(query_volume, []):
                    if len(cand) < MIN_CANDIDATE_LEN:
                        continue
                    if EXCLUDED_CANDIDATE_RE.search(cand):
                        continue
                    if product_variant_markers(query) != product_variant_markers(cand):
                        continue
                    if not covers_content_words(query, cand):
                        continue
                    # Кандидат не должен нести ЛИШНИЕ отличительные слова — иначе
                    # "Кофе Капучино" находил бы "Кофе Карамельный Капучино" (тоже
                    # покрывает запрос, но добавляет вкус, которого не было) — нашёл
                    # 2026-08-31. Предпочитаем кандидата с минимумом лишних слов, затем
                    # с непустой classification (в product_classification полно "мёртвых"
                    # дублей-двойников с classification=NULL и 0 продаж, которые иногда
                    # текстово ближе к ТТК-написанию, чем реальный активный товар —
                    # напр. 'Кофе "Капучино" 0.2' (мёртвый, кавычки как в ТТК) vs
                    # 'Кофе Капучино 0.2' (реальный, Обязательный ассортимент)).
                    extra = len(set(content_words(cand)) - query_words)
                    no_classification = 0 if product_names[cand] in has_classification else 1
                    score = fuzz.WRatio(query, cand)
                    key = (extra, no_classification, -score)
                    if best is None or key < (best[1], best[2], -best[3]):
                        best = (cand, extra, no_classification, score)
                if best:
                    accepted, matched_name = True, best[0]

        if not accepted:
            unmatched_products.append(recipe["reestr_name"])
            continue
        product_uuid = product_names[matched_name]

        size_match = DOUGH_SIZE_RE.search(recipe["title"] or "") or DOUGH_SIZE_RE.search(recipe["reestr_name"])
        dough_size = size_match.group(1) if size_match else None

        for ing_name, netto in recipe["ingredients"]:
            ing_norm = norm(ing_name)

            if ing_norm in DOUGH_GENERIC_NAMES and dough_size:
                sized_query = f"тесто {dough_size}"
                ing_result = process.extractOne(sized_query, material_name_list, scorer=fuzz.WRatio)
                if not (ing_result and ing_result[1] >= 95):
                    unmatched_ingredients[f"{ing_name} [размер {dough_size} не найден в справочнике]"] += 1
                    continue
                material_uuid = material_names[ing_result[0]]
            elif ing_norm in CONFIRMED_INGREDIENT_MATCHES:
                target_query = CONFIRMED_INGREDIENT_MATCHES[ing_norm]
                ing_result = process.extractOne(target_query, material_name_list, scorer=fuzz.WRatio)
                if not (ing_result and ing_result[1] >= 90):
                    unmatched_ingredients[ing_name] += 1
                    continue
                material_uuid = material_names[ing_result[0]]
            else:
                ing_result = process.extractOne(ing_norm, material_name_list, scorer=fuzz.WRatio)
                if not (ing_result and ing_result[1] >= INGREDIENT_MATCH_THRESHOLD):
                    unmatched_ingredients[ing_name] += 1
                    continue
                material_uuid = material_names[ing_result[0]]

            key = (product_uuid, material_uuid)
            if key not in rows or (netto and netto > (rows[key] or 0)):
                rows[key] = netto

    print(f"\nПродукты: {len(all_recipes) - len(unmatched_products)}/{len(all_recipes)} сопоставлены")
    print(f"Непойманные продукты ({len(unmatched_products)}): {unmatched_products[:10]}{'...' if len(unmatched_products) > 10 else ''}")
    total_ing_mentions = sum(unmatched_ingredients.values()) + len(rows)
    print(f"Строк рецептуры (product_uuid, material_uuid) собрано: {len(rows)}")
    print(f"Непойманные ингредиенты (уникальных названий): {len(unmatched_ingredients)}")
    for name, count in sorted(unmatched_ingredients.items(), key=lambda x: -x[1])[:30]:
        print(f"  '{name}' (встречается в {count} рецептах)")

    if args.dry_run:
        print("\n--dry-run: в БД ничего не записано.")
        conn.close()
        return

    values = [(pu, mu, netto) for (pu, mu), netto in rows.items()]
    # Свежее соединение для записи: matching (особенно volume-aware rescue,
    # process.extract по большому списку кандидатов на каждый непойманный продукт)
    # занимает 1-2 минуты, и старое соединение к этому моменту иногда обрывается
    # Supabase pooler'ом по idle timeout (SSL error: unexpected eof — поймано 2026-08-31).
    conn.close()
    conn = get_connection()
    cur = get_cursor(conn)
    # TRUNCATE, не upsert: перезапуск с более строгим порогом/списком исключений должен
    # полностью заменить содержимое, а не оставлять поверх старые (возможно ошибочные —
    # см. предупреждение про WRatio-порог в начале файла) строки от прошлого запуска.
    cur.execute("TRUNCATE TABLE product_recipe")
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO product_recipe (product_uuid, material_uuid, netto_grams) VALUES %s",
        values,
        template="(%s, %s, %s)",
        page_size=1000,
    )
    conn.commit()
    conn.close()
    print(f"\nЗаписано {len(values)} строк в product_recipe (таблица перезалита с нуля).")


if __name__ == "__main__":
    main()
