-- discount_category_daily: сумма скидки по типу акции (не по товарной категории)
-- за день/точку — питает блок "Дисконт по категориям" в pulse-week.
--
-- Категория — эвристическая классификация accounting/sales'ного
-- discount.bonusActionName (см. classify_discount() в get_product_sales.py):
-- точного справочника типов скидок DodoIS (Комбо/Додокоины/CVM/Другое + подтипы,
-- как в их UI) в API нет, так что это приближение по префиксам/ключевым словам,
-- не побайтовое соответствие.

CREATE TABLE IF NOT EXISTS discount_category_daily (
    date              date    NOT NULL,
    unit_id           text    NOT NULL,
    category          text    NOT NULL,
    discount_amount   numeric NOT NULL DEFAULT 0,
    PRIMARY KEY (date, unit_id, category)
);

CREATE INDEX IF NOT EXISTS idx_discount_category_daily_unit_date
    ON discount_category_daily (unit_id, date);

ALTER TABLE discount_category_daily ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON discount_category_daily
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE discount_category_daily TO anon;
