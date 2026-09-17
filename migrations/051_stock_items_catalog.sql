-- stock_items_catalog: полный справочник сырья/товаров из Dodo IS Accounting API
-- (GET /accounting/stock-items — "старый"/fallback контракт, работает с нашим
-- существующим OAuth-токеном; новый GET {accounting-root}/catalogs/stock-items
-- даёт 403 — видимо, для него нужен отдельный scope, которого у нашего клиента нет).
--
-- Найдено 2026-08-31 через реверс-инжиниринг открытого проекта 2mozg-oss
-- (github.com/SergGT/2mozg-oss, Apache-2.0, src/integrations/dodo_api.py
-- DodoAPIClient.get_stock_items) — у него было рабочее обращение к этому же
-- эндпоинту, что и навело на мысль проверить его с нашим токеном.
--
-- Кардинально полнее material_classification (235 строк, только классификация
-- категорий А/В/С) и даже write-offs-based fallback из issue #6 (250 строк):
-- 1466 позиций всего, 330 category_name='Ingredient' + 66 'SemiFinishedProduct'.
-- См. load_stock_items_catalog.py и issue #6.

CREATE TABLE IF NOT EXISTS stock_items_catalog (
    stock_item_id   text PRIMARY KEY,
    name            text,
    category_name   text,  -- Ingredient / SemiFinishedProduct / FinishedProduct / Packing / Consumables / Inventory
    measurement_unit text,
    modified_at     timestamp,
    loaded_at       timestamp NOT NULL DEFAULT now()
);

ALTER TABLE stock_items_catalog ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON stock_items_catalog
    FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON stock_items_catalog
    FOR SELECT TO authenticated USING (true);

GRANT SELECT ON TABLE stock_items_catalog TO anon;
GRANT SELECT ON TABLE stock_items_catalog TO authenticated;
