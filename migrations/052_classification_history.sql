-- История статусов справочников продуктов и материалов (SCD-2).
--
-- Зачем: справочник «Справочник ингредиентов и продуктов» в Dodo IS — НЕ статический
-- реестр, а скользящий снимок. Дословно из KB-статьи «Дашборд "Стопы продуктов и
-- ингредиентов"» (f878a355-b8b8-4263-9db1-73e3c7b9cc2e):
--
--   «Справочник ингредиентов и продуктов включает статусы по всем продуктам и
--    ингредиентам, по которым был расход за последние 21 день. Если у ингредиента
--    или продукта нет расхода во всей сети за 21 день, то по умолчанию стоит статус
--    "Распродажа"». Статусы мигрируют: Тест → Новинка → Обязательный ассортимент →
--    Распродажа.
--
-- Из-за этого одна ручная выгрузка не позволяет воспроизвести прошлый период: метрика
-- «Стопы ключевых ингредиентов» фильтрует стопы по статусу продукта, а статус нужно
-- брать НА ДАТУ СТОПА, а не текущий. Именно здесь сидела основная часть расхождения с
-- Dodo IS (35% на августе 2026) — см. docs/stops-lost-revenue-reconciliation.md.
--
-- Плоские таблицы product_classification / material_classification остаются как есть
-- («последний снимок»): их читает фронтенд для названий и категорий, где история не
-- нужна. Здесь копится только то, что меняется во времени и влияет на расчёт.
--
-- valid_to = NULL означает «строка актуальна на последнюю загрузку». Интервал
-- полуоткрытый: [valid_from, valid_to), то есть в день valid_to действует уже
-- следующая версия.

CREATE TABLE IF NOT EXISTS product_classification_history (
    product_uuid   text NOT NULL,
    valid_from     date NOT NULL,
    valid_to       date,
    classification text,   -- Тест / Новинка / Обязательный ассортимент / Необязательный ассортимент / Распродажа
    category_name  text,
    product_name   text,
    PRIMARY KEY (product_uuid, valid_from)
);

CREATE INDEX IF NOT EXISTS product_classification_history_lookup
    ON product_classification_history (product_uuid, valid_from DESC);

CREATE TABLE IF NOT EXISTS material_classification_history (
    material_uuid  text NOT NULL,
    valid_from     date NOT NULL,
    valid_to       date,
    classification text,   -- Категория А/В/С / Сезонный / Локальные / Распродажа
    material_name  text,
    PRIMARY KEY (material_uuid, valid_from)
);

CREATE INDEX IF NOT EXISTS material_classification_history_lookup
    ON material_classification_history (material_uuid, valid_from DESC);

ALTER TABLE product_classification_history  ENABLE ROW LEVEL SECURITY;
ALTER TABLE material_classification_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON product_classification_history
    FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON product_classification_history
    FOR SELECT TO authenticated USING (true);
CREATE POLICY anon_read ON material_classification_history
    FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON material_classification_history
    FOR SELECT TO authenticated USING (true);

GRANT SELECT ON TABLE product_classification_history  TO anon, authenticated;
GRANT SELECT ON TABLE material_classification_history TO anon, authenticated;
