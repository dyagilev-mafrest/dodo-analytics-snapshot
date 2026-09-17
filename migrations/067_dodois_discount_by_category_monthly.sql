-- Переименование dodois_discount_share_monthly (066) в
-- dodois_discount_by_category_monthly и вторая мера — рубли.
--
-- 066 завела таблицу под один экспорт Dodo IS Superset — «Share of Orders with
-- discount by Type and subcategory». Через час выяснилось, что рядом живёт
-- второй дашборд того же семейства, «Discount by DodoIs category in dynamic», и
-- отдаёт он ТОТ ЖЕ разрез (месяц × тип × подкатегория), только в рублях. Две
-- таблицы на одно зерно разошлись бы при первой же правке подкатегорий, поэтому
-- меры живут колонками одной строки, а имя перестало называть одну из них.
--
-- Обе меры необязательны: выгрузки делаются порознь и фильтруются по-разному.
-- Ту, что по рублям, руководство берёт сразу по одной подкатегории
-- («Локальные»), и требовать к ней долю заказов значило бы не дать загрузить
-- присланное.
--
-- ⚠️ Меры отвечают на РАЗНЫЕ вопросы и не выводятся одна из другой:
-- order_share — в скольких заказах была скидка этого вида, discount_rub —
-- сколько денег на неё ушло. За август 2026 «Другое, Локальные» это 0,42%
-- заказов и 274 277 ₽. Долю от выручки дашборд считает сам: делит рубли на
-- нашу выручку без дисконта — ту же базу, что у дисконта общего, чтобы строки
-- в отчёте были сравнимы.
ALTER TABLE IF EXISTS dodois_discount_share_monthly
    RENAME TO dodois_discount_by_category_monthly;

ALTER TABLE dodois_discount_by_category_monthly
    ADD COLUMN IF NOT EXISTS discount_rub numeric;

ALTER TABLE dodois_discount_by_category_monthly
    ALTER COLUMN order_share DROP NOT NULL;

-- Строка без единой меры — мусор: она означала бы «подкатегория была, а данных
-- нет», чего ни один из двух экспортов сказать не может.
ALTER TABLE dodois_discount_by_category_monthly
    DROP CONSTRAINT IF EXISTS discount_by_category_has_measure;
ALTER TABLE dodois_discount_by_category_monthly
    ADD CONSTRAINT discount_by_category_has_measure
    CHECK (order_share IS NOT NULL OR discount_rub IS NOT NULL);
