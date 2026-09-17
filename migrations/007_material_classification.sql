-- material_classification: справочник материалов/ингредиентов из Dodo BI
-- ("Dictionary of the materials" xlsx, экспортируется вручную командой контроллинга,
-- нет API-эндпоинта). material_uuid соответствует ingredientId из
-- production/stop-sales-ingredients (регистр приводится к нижнему для join).
-- Классификация (Категория А/В/С, Сезонный, Локальные) используется для разбивки
-- стопов ингредиентов — см. load_material_classification.py.

CREATE TABLE IF NOT EXISTS material_classification (
    material_uuid     text PRIMARY KEY,
    material_name     text,
    material_category text,   -- Ингредиент / Готовая продукция / Полуфабрикат / Упаковка / Расходники
    rating_category   integer,
    classification    text,   -- Категория А/В/С / Сезонный ингредиент / Локальные ингредиенты
    consumption_21d   numeric,
    uploaded_at        date
);

ALTER TABLE material_classification ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON material_classification
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE material_classification TO anon;
