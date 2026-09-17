-- Упущенная выручка от стопов ПРОДУКТА, у которых нет ингредиентной причины.
--
-- Зачем отдельно от ingredient_stop_impact: метрика «Стопы продуктов» у Dodo IS
-- складывается из двух разных явлений. Первое — стоп ингредиента, развёрнутый по
-- рецептуре на затронутые продукты (ingredient_stop_impact). Второе — продукт
-- выключили сам по себе, ингредиента за ним нет: в выгрузке «Детали каждого стопа»
-- у таких строк колонка «Наименование ингредиента» пустая. За 01.07-31.08.2026 это
-- 595 строк и 875 699 ₽ — 62% всего, что у них есть, а у нас нет.
--
-- Правило отбора: берём стоп продукта, если ни на один его день нет ингредиентного
-- вклада по той же паре (пиццерия, продукт). Проверено против эталона: ловит 480 из
-- 592 их ключей (81%), а ложные срабатывания стоят 4 983 ₽ на 2 030 ключей — то есть
-- практически бесплатны. Раньше это же правило давало 70% на одной неделе и 148% на
-- другой, потому что «нет ингредиентного вклада» означало «ингредиента нет в карте
-- рецептур»; после загрузки полной карты (source uc-export) оно наконец измеряет
-- явление, а не дыру в справочнике.
--
-- Хранение ПОДНЕВНОЕ, как у ingredient_stop_impact (миграция 059): стоп живёт
-- неделями, и привязка всей суммы к дате начала ломает форму по периодам.

CREATE TABLE IF NOT EXISTS product_stop_impact (
    stop_event_id text        NOT NULL,
    unit_id       text        NOT NULL,
    product_uuid  text        NOT NULL,
    stop_date     date        NOT NULL,
    lost_revenue  numeric     NOT NULL,
    reason        text,
    -- Классификация продукта на дату стопа: статус меняется во времени, и «сейчас»
    -- регулярно не равно «тогда» (та же логика, что в ingredient_stop_impact).
    product_classification text,
    PRIMARY KEY (stop_event_id, stop_date)
);

CREATE INDEX IF NOT EXISTS product_stop_impact_date_idx ON product_stop_impact (stop_date);
CREATE INDEX IF NOT EXISTS product_stop_impact_unit_idx ON product_stop_impact (unit_id, product_uuid, stop_date);

ALTER TABLE product_stop_impact ENABLE ROW LEVEL SECURITY;
-- Дашборд читает под anon и под authenticated — нужны обе политики, иначе
-- залогиненный пользователь получает 403 (см. память feedback_supabase_rls_both_roles).
DROP POLICY IF EXISTS product_stop_impact_read_anon ON product_stop_impact;
CREATE POLICY product_stop_impact_read_anon ON product_stop_impact FOR SELECT TO anon USING (true);
DROP POLICY IF EXISTS product_stop_impact_read_auth ON product_stop_impact;
CREATE POLICY product_stop_impact_read_auth ON product_stop_impact FOR SELECT TO authenticated USING (true);
GRANT SELECT ON product_stop_impact TO anon, authenticated;
