-- Эталон Dodo IS по стопам ПИЦЦЕРИЙ и СЕКТОРОВ (дашборд «Стопы пиццерий и активность
-- секторов доставки»), ручная выгрузка. Аналог 040/041, но для другой метрики: те
-- таблицы держат стопы продуктов и ключевых ингредиентов.
--
-- Зачем: наш расчёт (compute_stop_lost_revenue.py) до сих пор сверялся с Dodo IS
-- разово и «на глаз» — цифры сверки нигде не хранились, повторить её было нечем.
-- Сюда кладём выгрузку как есть, чтобы analyze_unit_sector_stops_calibration.py мог
-- гонять варианты формулы против неизменного эталона.
--
-- Гранулярность периода произвольная (period_start..period_end включительно): Dodo IS
-- отдаёт и недельные, и месячные срезы, а сверка методики идёт по неделям.
-- ⚠️ В фильтрах дашборда правая дата НЕ включается: за 03–09.08 выбирать 03.08–10.08.
-- В таблицу пишем период уже в наших, включительных границах.
--
-- exported_at обязателен: цифра Dodo IS дозревает неделями после конца периода
-- (см. docs/stops-lost-revenue-reconciliation.md, правило №1) — без даты выгрузки
-- нельзя понять, дозрела ли строка.
CREATE TABLE IF NOT EXISTS dodois_unit_sector_stops (
    period_start        date NOT NULL,
    period_end          date NOT NULL,  -- включительно
    unit_name           text NOT NULL,
    revenue_rub         numeric,        -- знаменатель выручки из той же выгрузки
    -- доли, в процентах (0-100)
    lost_pct_total      numeric,        -- «пиццерия + сектор», то, что показывает дашборд
    lost_pct_unit       numeric,        -- стопы пиццерии
    lost_pct_sector     numeric,        -- стопы секторов
    lost_pct_subsector  numeric,        -- стопы подсекторов (в Якутске не встречались)
    -- ₽: из выгрузки, либо выведено из доли и выручки при загрузке
    lost_rub_total      numeric,
    lost_rub_unit       numeric,
    lost_rub_sector     numeric,
    lost_rub_subsector  numeric,
    -- время в стопе (в РАБОЧИЕ часы — так его считает сам Dodo IS) и количество стопов
    stop_minutes_unit      numeric,
    stop_minutes_sector    numeric,
    stop_minutes_subsector numeric,
    stop_count_unit      integer,
    stop_count_sector    integer,
    stop_count_subsector integer,
    exported_at         date NOT NULL,  -- когда выгрузили из Dodo IS
    loaded_at           timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (period_start, period_end, unit_name)
);

ALTER TABLE dodois_unit_sector_stops ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_unit_sector_stops FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_unit_sector_stops FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_unit_sector_stops TO anon;
GRANT SELECT ON TABLE dodois_unit_sector_stops TO authenticated;
