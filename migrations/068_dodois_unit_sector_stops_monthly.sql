-- Упущенная выручка от стопов ПИЦЦЕРИЙ и СЕКТОРОВ по сети, помесячно — ручной
-- экспорт Dodo IS Superset: «Динамика упущенной выручки (стопы пиццерий new)»
-- (рубли по трём уровням) и «Динамика % упущенной выручки (все стопы)» (доля
-- «пиццерия + сектор»).
--
-- Почему не в dodois_unit_sector_stops (054): та таблица заведена под сверку
-- методики — зерно «период × пиццерия», периоды произвольные, недельные. Здесь
-- сеть целиком и ровно месяц, это витрина месячного отчёта, а не эталон для
-- калибровки. Сложить их в одну значило бы держать в PK то unit_name='Сеть',
-- то настоящее имя.
--
-- Аналог 040 (стопы продуктов), только для другого уровня стопов.
--
-- ДОЛИ ПО УРОВНЯМ ВЫВЕДЕНЫ ПРИ ЗАГРУЗКЕ. Выгрузка даёт рубли отдельно по
-- пиццериям, секторам и подсекторам, а долю — только общую. Знаменатель у неё
-- «выручка + упущенное» (проверено на августе 2026: 4 506 438 / 0,0288191 =
-- 156 369 697 = выручка 151 863 259 плюс упущенное), поэтому доля уровня
-- считается как pct_total * rub_уровня / rub_всего — точно, без обращения к
-- нашей выручке.
--
-- Подсектора в Якутске почти всегда нули: уровень в сети есть, у нас не
-- используется. Колонка оставлена, чтобы выгрузку не приходилось резать.
CREATE TABLE IF NOT EXISTS dodois_unit_sector_stops_monthly (
    month              date PRIMARY KEY,  -- первое число месяца
    lost_rub_unit      numeric,           -- стопы пиццерий
    lost_rub_sector    numeric,           -- стопы секторов
    lost_rub_subsector numeric,           -- стопы подсекторов
    -- доли, в процентах (0-100), как везде в кодовой базе
    lost_pct_total     numeric,           -- «пиццерия + сектор», как показывает дашборд
    lost_pct_unit      numeric,           -- выведено при загрузке
    lost_pct_sector    numeric,           -- выведено при загрузке
    loaded_at          timestamp NOT NULL DEFAULT now()
);

ALTER TABLE dodois_unit_sector_stops_monthly ENABLE ROW LEVEL SECURITY;

-- Обе роли обязательны: anon читает публично, authenticated — залогиненные в
-- дашборд. Без второй политики они получают 403 (грабли, описанные в agents.md).
CREATE POLICY anon_read ON dodois_unit_sector_stops_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_unit_sector_stops_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_unit_sector_stops_monthly TO anon;
GRANT SELECT ON TABLE dodois_unit_sector_stops_monthly TO authenticated;
