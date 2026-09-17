-- product_recipe.source: откуда взялась связь «продукт ← ингредиент».
--
-- Зачем понадобилось (найдено 09.09.2026 при сверке стопов продуктов с Dodo IS):
-- ТТК-матчинг (parse_ttk_recipes.py) сопоставляет названия из xlsx с
-- product_classification по fuzzy-match, а в справочнике 8206 продуктов, из
-- которых реально продаётся 818. Однофамильцев много («Пепперони 30» как
-- обычная, halal, для персонала, ДодоБокс), и матчинг систематически цеплялся
-- за неторгуемый двойник.
--
-- Итог был тихим и разрушительным: из 252 продуктов в product_recipe только 58
-- присутствовали в product_daily_revenue. У остальных база выручки пустая,
-- avg_prior_same_weekday_revenue возвращала 0, и упущенная выручка по стопу
-- выходила РОВНО НОЛЬ. На сверке это выглядело так: 568 из 603 стопов Dodo IS
-- совпали с нашими по времени с точностью до секунды, но денег на них у нас
-- было 311 378 ₽ против их 2 590 242 ₽ — 12%. Самые дорогие стопы («Соус
-- Цезарь порционный» 437 994 ₽, «Сыр Блю Чиз» 301 220 ₽) стояли ровно на нуле.
--
-- Поэтому связи теперь бывают двух происхождений, и их надо различать:
--   'ttk'            — fuzzy-match по ТТК-файлам, есть риск однофамильца
--   'dodois-export'  — из выгрузки Dodo IS «Stops of products details», где
--                      ProductUUid приходит готовым, без сопоставления имён.
--                      Эталонное происхождение: 308 из 309 их продуктов есть
--                      в product_daily_revenue.
--
-- Практика: при конфликте (одна и та же пара из двух источников) сохраняется
-- существующая строка — загрузчик из выгрузки не перетирает ТТК-нетто.

ALTER TABLE product_recipe
    ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'ttk';

COMMENT ON COLUMN product_recipe.source IS
    'ttk — fuzzy-match по ТТК-файлам; dodois-export — ProductUUid из выгрузки Dodo IS';

-- Диагностика, ради которой всё и делалось: связи, чей продукт никогда не
-- встречался в product_daily_revenue, дают упущенную выручку 0 и потому
-- бесполезны. Индекс не нужен, но представление удобно держать под рукой.
CREATE OR REPLACE VIEW product_recipe_without_revenue AS
SELECT pr.product_uuid, pr.material_uuid, pr.source,
       pc.product_name, pc.classification
FROM product_recipe pr
LEFT JOIN product_classification pc ON pc.product_uuid = pr.product_uuid
WHERE NOT EXISTS (
    SELECT 1 FROM product_daily_revenue r WHERE r.product_id = pr.product_uuid
);
