-- Классификация, разрешённая НА ДАТУ СТОПА, прямо в строках ingredient_stop_impact.
--
-- Метрика «Стопы ключевых ингредиентов» = стопы материалов «Категория А» по продуктам
-- со статусом «Новинка»/«Обязательный ассортимент» (дословно из KB-статьи
-- f878a355-b8b8-4263-9db1-73e3c7b9cc2e). Но справочник статусов — скользящий снимок
-- (окно «расход за 21 день»), поэтому джойн к ТЕКУЩЕЙ классификации отвечает на вопрос
-- «чем позиция является сейчас», а нужен ответ «чем она была в день стопа».
--
-- Разрешаем это один раз на бэкенде (compute_ingredient_stop_impact.py по
-- *_classification_history) и храним результат здесь. Так фронтенд не тащит всю
-- историю в браузер и не повторяет логику интервалов, а пересчёт прошлых периодов
-- остаётся воспроизводимым.
ALTER TABLE ingredient_stop_impact
    ADD COLUMN IF NOT EXISTS material_classification text,
    ADD COLUMN IF NOT EXISTS product_classification  text;

-- Сколько строк посчитано по «настоящей» истории, а сколько по ближайшему известному
-- снимку (для стопов раньше первой выгрузки справочника) — чтобы точность расчёта
-- была видна, а не подразумевалась.
ALTER TABLE ingredient_stop_impact
    ADD COLUMN IF NOT EXISTS classification_exact boolean;

CREATE INDEX IF NOT EXISTS ingredient_stop_impact_key_metric
    ON ingredient_stop_impact (material_classification, product_classification);
