-- productivity_stats: kitchen staff productivity per unit per day
-- Source: Dodo IS API  production/productivity
-- Scope: productionefficiency
--
-- productsPerLaborHour and salesPerLaborHour are ratios from the API.
-- To aggregate across units/days, weight by labor_hours:
--   net_products_per_lh = SUM(products_per_labor_hour * labor_hours) / SUM(labor_hours)
--   net_sales_per_lh    = SUM(sales) / SUM(labor_hours)

CREATE TABLE IF NOT EXISTS productivity_stats (
    date                    date    NOT NULL,
    unit_id                 text    NOT NULL,
    unit_name               text,
    labor_hours             real,   -- total kitchen + management staff hours
    sales                   real,   -- total revenue (₽)
    products_per_labor_hour real,   -- products through oven / labor_hours
    sales_per_labor_hour    real,   -- sales / labor_hours  (₽ / чел-час)
    avg_heated_shelf_time   integer,-- seconds (delivery shelf wait)
    orders_per_courier_hour real,   -- delivery orders / courier labor hours
    PRIMARY KEY (date, unit_id)
);

-- RLS
ALTER TABLE productivity_stats ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON productivity_stats
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE productivity_stats TO anon;
