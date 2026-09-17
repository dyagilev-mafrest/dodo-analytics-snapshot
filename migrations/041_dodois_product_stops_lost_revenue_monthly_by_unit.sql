-- Per-unit companion to dodois_product_stops_lost_revenue_monthly (040) — powers
-- the "По пиццериям" view of the "Стопы продуктов и ингредиентов" trend charts
-- in dodo-analytics-dashboard's "Месяц" section. Same rationale/caveats as 040:
-- ручной ежемесячный экспорт из Dodo IS Superset, temporary override while our
-- own unit+reason+time-overlap heuristic overcounts on long periods.
--
-- Source export gives revenue + share (fraction of 1) per unit per month —
-- lost_revenue_rub is DERIVED at load time from share/revenue:
--   share = lost / (lost + revenue)  =>  lost = share * revenue / (1 - share)
CREATE TABLE IF NOT EXISTS dodois_product_stops_lost_revenue_monthly_by_unit (
    month                          date NOT NULL,  -- первое число месяца
    unit_name                      text NOT NULL,
    revenue_rub                    numeric,
    lost_revenue_products_pct      numeric,        -- доля, в процентах (0-100)
    lost_revenue_key_ingredients_pct numeric,
    lost_revenue_products_rub      numeric,        -- вычислено из pct+revenue
    lost_revenue_key_ingredients_rub numeric,
    loaded_at                      timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, unit_name)
);

ALTER TABLE dodois_product_stops_lost_revenue_monthly_by_unit ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_product_stops_lost_revenue_monthly_by_unit FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_product_stops_lost_revenue_monthly_by_unit FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_product_stops_lost_revenue_monthly_by_unit TO anon;
GRANT SELECT ON TABLE dodois_product_stops_lost_revenue_monthly_by_unit TO authenticated;
