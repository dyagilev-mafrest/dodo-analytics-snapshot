-- ingredient_stop_impact: упущенная выручка КАЖДОГО продукта, реально связанного
-- (через product_recipe, из ТТК) с конкретным ингредиентным стопом, посчитанная по той
-- же формуле, что и обычные продуктовые стопы (compute_lost_revenue.py), но с окном
-- времени ИНГРЕДИЕНТНОГО стопа как триггером, а не через поиск совпадающей по времени
-- строки в stop_events(event_type='product').
--
-- Почему нужна отдельная таблица (см. issue #6, память
-- project_dodo_stops_products_ingredients.md, сессия 2026-08-31): выяснилось, что наш
-- API-фид продуктовых стопов даёт в 3-70 раз МЕНЬШЕ событий, чем Dodo IS считает у себя
-- (Dodo IS каскадирует стоп ингредиента на продукты автоматически внутри своей системы;
-- наш API отдаёт только "буквальные" продуктовые стопы). Поэтому подход "искать
-- пересекающийся по времени product-стоп" почти всегда не находит совпадения — нужно
-- считать упущенную выручку продукта САМИМ, используя окно ингредиентного стопа.
--
-- Один ингредиентный стоп может дать много строк (по одной на каждый связанный продукт).
CREATE TABLE IF NOT EXISTS ingredient_stop_impact (
    stop_event_id text NOT NULL,  -- stop_events.id (text, не integer)
    product_uuid  text NOT NULL,
    lost_revenue  numeric NOT NULL,
    PRIMARY KEY (stop_event_id, product_uuid)
);

ALTER TABLE ingredient_stop_impact ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON ingredient_stop_impact
    FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON ingredient_stop_impact
    FOR SELECT TO authenticated USING (true);

GRANT SELECT ON TABLE ingredient_stop_impact TO anon;
GRANT SELECT ON TABLE ingredient_stop_impact TO authenticated;
