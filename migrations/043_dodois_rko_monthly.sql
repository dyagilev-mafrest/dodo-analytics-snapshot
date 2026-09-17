-- РКО (рейтинг клиентского опыта) — ручной ежемесячный экспорт из Dodo IS
-- Superset, источник эндпоинт controlling/ratings/customer-experience
-- (см. память project_dodo_quality_api_endpoints — ранее сознательно вне
-- скоупа, требовал нового ETL; пока используем ручной экспорт вместо live-пула
-- API, тот же временный подход, что у dodois_product_stops_lost_revenue_monthly).
-- Powers "Динамика РКО" в разделе "Месяц" (dodo-analytics-dashboard),
-- "Клиентский опыт". Сеть целиком, без разбивки по пиццериям — у обоих
-- присланных экспортов её нет. Только текущий год — таблица держится крошечной
-- (единицы строк/год) по требованию не раздувать Supabase (лимит 500 МБ).
CREATE TABLE IF NOT EXISTS dodois_rko_monthly (
    month                    date PRIMARY KEY,  -- первое число месяца
    pizzeria_count           integer,           -- "Кол-во пиццерий" (проверенных в этом периоде)
    avg_rko                  numeric,           -- "Средний РКО" (0-100)
    last12_pizzeria_count    integer,           -- "Кол-во пиццерий 12 последних проверок"
    avg_rko_last12           numeric,           -- "Средний РКО (12 проверок)" — сглаженный/rolling вариант
    loaded_at                timestamp NOT NULL DEFAULT now()
);

ALTER TABLE dodois_rko_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_rko_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_rko_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_rko_monthly TO anon;
GRANT SELECT ON TABLE dodois_rko_monthly TO authenticated;
