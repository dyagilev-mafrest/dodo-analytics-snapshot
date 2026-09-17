-- LFL заказов/выручки по стране (Dodo IS считает это сам — не наш расчёт),
-- источник: GET https://api.dodois.io/customer-feedback/lfl/by-countries
-- ?countries=RU&granularity=day|week|month&from=...&to=...
--
-- Возвращается уже готовый % роста к тому же периоду год назад (lflOrder,
-- lflRevenue — доли, не проценты, например -0.06 = -6%). Один запрос на
-- гранулярность отдаёт сразу весь диапазон дат, без пагинации.
--
-- Храним все три гранулярности отдельными строками (day/week/month), т.к.
-- недельный/месячный LFL — не среднее по дням, а отдельный расчёт Dodo IS
-- (нельзя корректно усреднить дневные проценты в недельные на нашей стороне).
-- Используется как серия "РФ" в переключателе чарта "LFL заказов" в блоке
-- "Продажи" (dodo-analytics-dashboard) — доступа к данным по регионам/ДФО
-- у нашего OAuth-клиента нет (проверено: 403 на accounting/sales для чужих
-- юнитов, эндпоинтов вида lfl/by-regions не существует).

CREATE TABLE IF NOT EXISTS country_lfl (
    country_id   text    NOT NULL,
    granularity  text    NOT NULL CHECK (granularity IN ('day', 'week', 'month')),
    date         date    NOT NULL,
    lfl_orders   numeric NOT NULL,
    lfl_revenue  numeric NOT NULL,
    PRIMARY KEY (country_id, granularity, date)
);

ALTER TABLE country_lfl ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON country_lfl
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE country_lfl TO anon;
