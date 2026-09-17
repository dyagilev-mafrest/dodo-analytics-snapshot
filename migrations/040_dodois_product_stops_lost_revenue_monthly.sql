-- ВРЕМЕННЫЙ источник истины для метрик «Стопы продуктов»/«Ключевые ингредиенты»
-- в разделе «Месяц» (dodo-analytics-dashboard, PulseMonthView) — ручной
-- ежемесячный экспорт из Dodo IS Superset dashboard 111 (два виджета
-- "Lost Sales for Stops of ingridients and products Dynamics" и
-- "Lost Sales for Stops of key ingridients", оба ₽ и %).
--
-- Причина: наша собственная эвристика сопоставления ингредиент↔продукт
-- (юнит+причина+пересечение времени, см. usePulseWeekStopsProductsData.ts)
-- даёт крупный перелёт по «ключевым ингредиентам» на длинных периодах —
-- см. память project_dodo_stops_products_ingredients (сессия 2026-08-25:
-- на июле 2026 перелёт 180%, часть — двойной счёт, часть — сопоставление
-- по обобщённой причине с неродственными товарами). Без таблицы рецептов
-- (состав продукта → ингредиенты) точно повторить формулу Dodo IS нельзя.
--
-- Решение пользователя: пока подставляем цифры Dodo IS напрямую как временное
-- решение, к эвристике возвращаемся после месячного совета руководителей.
-- Не путать с product_daily_revenue/hourly_revenue_by_channel — не бэкфиллится
-- автоматикой, обновляется вручную через load_dodois_stop_lost_revenue_monthly.py.
CREATE TABLE IF NOT EXISTS dodois_product_stops_lost_revenue_monthly (
    month                          date PRIMARY KEY,  -- первое число месяца
    lost_revenue_products_rub      numeric,
    lost_revenue_key_ingredients_rub numeric,
    lost_revenue_products_pct      numeric,           -- доля, в процентах (0-100, не доля от 1)
    lost_revenue_key_ingredients_pct numeric,
    loaded_at                      timestamp NOT NULL DEFAULT now()
);

ALTER TABLE dodois_product_stops_lost_revenue_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_product_stops_lost_revenue_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_product_stops_lost_revenue_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_product_stops_lost_revenue_monthly TO anon;
GRANT SELECT ON TABLE dodois_product_stops_lost_revenue_monthly TO authenticated;
