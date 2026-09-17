-- Упущенная выручка по категориям меню (Десерты/Закуски/Кусочки/Напитки/Пицца/
-- Соусы) — ручной ежемесячный экспорт из Dodo IS Superset, powers "Динамика
-- упущенной выручки по категориям" в подблоке "Стопы продуктов и ингредиентов"
-- (dodo-analytics-dashboard, PulseMonthView, раздел "Месяц"). Тот же временный-
-- override рационал, что у 040/041 — см. память project_dodo_stops_products_ingredients.
-- Melted (long) format, не одна строка на месяц с колонкой на категорию —
-- набор категорий у Dodo IS может меняться.
CREATE TABLE IF NOT EXISTS dodois_lost_revenue_by_category_monthly (
    month           date NOT NULL,  -- первое число месяца
    category_name   text NOT NULL,
    lost_revenue_rub numeric,
    loaded_at       timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, category_name)
);

ALTER TABLE dodois_lost_revenue_by_category_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_lost_revenue_by_category_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_lost_revenue_by_category_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_lost_revenue_by_category_monthly TO anon;
GRANT SELECT ON TABLE dodois_lost_revenue_by_category_monthly TO authenticated;
