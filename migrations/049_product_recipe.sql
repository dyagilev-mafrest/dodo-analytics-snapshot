-- product_recipe: карта рецептур (продукт -> ингредиенты), построенная из
-- официальных ТТК-файлов ("ТТК Пиццы/Закуски/Напитки Евразия", ручной xlsx-экспорт,
-- нет API-эндпоинта с рецептурой). product_uuid/material_uuid сопоставлены с
-- ТТК-названиями через fuzzy-match (rapidfuzz WRatio>=85) против
-- product_classification/material_classification (+ accounting/write-offs/stock-items
-- как fallback-справочник сырья) — см. parse_ttk_recipes.py и issue #6.
--
-- Используется, чтобы каскадировать стоп ингредиента на реально связанные продукты
-- (см. usePulseWeekStopsProductsData.ts в dodo-analytics-dashboard) вместо прежней
-- эвристики "тот же юнит + причина + пересечение времени", которая ловила случайные
-- совпадения по обобщённым причинам стопа.
--
-- Покрытие на момент первой загрузки (2026-08-31): ~99.8% продуктов, ~90% ингредиентов
-- по количеству уникальных названий в ТТК. Непойманные ингредиенты (сезонные лимитки,
-- новые п/ф без записи в справочниках) просто отсутствуют в этой таблице — их стопы
-- не каскадируются ни в один продукт.

CREATE TABLE IF NOT EXISTS product_recipe (
    product_uuid  text NOT NULL,
    material_uuid text NOT NULL,
    netto_grams   numeric,
    PRIMARY KEY (product_uuid, material_uuid)
);

ALTER TABLE product_recipe ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON product_recipe
    FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON product_recipe
    FOR SELECT TO authenticated USING (true);

GRANT SELECT ON TABLE product_recipe TO anon;
GRANT SELECT ON TABLE product_recipe TO authenticated;
