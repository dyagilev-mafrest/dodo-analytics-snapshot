-- product_classification: справочник продуктов из Dodo BI/Controlling
-- ("Subcategory dict" csv-экспорт со вкладки "Справочник ингредиентов и продуктов"
-- дашборда "Стопы продуктов и ингредиентов", нет API-эндпоинта — accounting/catalogs/stock-items
-- вернул 403, экспортируется вручную командой контроллинга).
-- product_uuid соответствует productId из production/stop-sales-products и
-- product_daily_revenue.product_id (регистр приводится к нижнему для join).
-- ClassificationNew (Тест/Новинка/Обязательный ассортимент/Необязательный ассортимент/
-- Распродажа) используется, чтобы отфильтровать "ключевые ингредиенты" — см.
-- compute_lost_revenue.py / KB-статью "Дашборд Стопы продуктов и ингредиентов":
-- в эту метрику входят стопы ингредиентов "Категория А" ТОЛЬКО по продуктам со
-- статусом "Новинка"/"Обязательный ассортимент".
-- CategoryNameRus (Пицца/Закуски/Напитки/...) закрывает отдельный пробел —
-- product_daily_revenue.product_category_name сейчас всегда NULL (баг ETL).

CREATE TABLE IF NOT EXISTS product_classification (
    product_uuid      text PRIMARY KEY,
    meta_product_name text,
    category_id       integer,
    category_name     text,   -- Пицца / Закуски / Напитки / Десерты / Кусочки / Соусы / Комбо / Товары
    product_name      text,
    subcategory       text,
    classification    text,   -- Тест / Новинка / Обязательный ассортимент / Необязательный ассортимент / Распродажа
    is_breakfast       boolean,
    consumption_21d   numeric,
    uploaded_at        date
);

ALTER TABLE product_classification ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON product_classification
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE product_classification TO anon;
