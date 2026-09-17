-- product_daily_revenue: выручка по конкретному продукту в конкретной точке за день
-- Source: Dodo IS API  accounting/sales (order-level, агрегируется скриптом get_product_sales.py
--         из вложенного products[] по priceWithDiscount)
-- Используется для расчёта упущенной выручки от стопов (compute_lost_revenue.py):
--   среднее revenue за 3 предыдущих таких же дня недели для (unit_id, product_id)

CREATE TABLE IF NOT EXISTS product_daily_revenue (
    date                   date    NOT NULL,
    unit_id                text    NOT NULL,
    product_id             text    NOT NULL,
    product_name           text,
    product_category_name  text,
    revenue                numeric NOT NULL DEFAULT 0,
    qty                    integer NOT NULL DEFAULT 0,
    PRIMARY KEY (date, unit_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_product_daily_revenue_unit_date
    ON product_daily_revenue (unit_id, date);

ALTER TABLE product_daily_revenue ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON product_daily_revenue
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE product_daily_revenue TO anon;
