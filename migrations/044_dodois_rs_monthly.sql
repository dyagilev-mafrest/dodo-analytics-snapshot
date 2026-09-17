-- РС (рейтинг стандартов) — ручной ежемесячный экспорт из Dodo IS Superset,
-- тот же источник (controlling/ratings/customer-experience) и тот же
-- временный подход, что у dodois_rko_monthly (043) — живой API-пул
-- недоступен, у нашего OAuth-приложения нет доступа к этому продукту API
-- (401/unauthorized_client, проверено 2026-08-25). Powers "Динамика РС" в
-- разделе "Месяц" (dodo-analytics-dashboard), "Операционная эффективность".
-- Сеть целиком, без разбивки по пиццериям. Только текущий год — таблица
-- держится крошечной по требованию не раздувать Supabase (лимит 500 МБ).
CREATE TABLE IF NOT EXISTS dodois_rs_monthly (
    month                 date PRIMARY KEY,  -- первое число месяца
    pizzeria_count        integer,           -- "Кол-во пиццерий" (проверенных в этом периоде)
    avg_rs                numeric,           -- "Средний РС" (0-100)
    last6_pizzeria_count  integer,           -- "Кол-во пиццерий 6 последних проверок"
    avg_rs_last6          numeric,           -- "Средний РС (6 проверок)" — сглаженный/rolling вариант
    loaded_at             timestamp NOT NULL DEFAULT now()
);

ALTER TABLE dodois_rs_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_rs_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_rs_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_rs_monthly TO anon;
GRANT SELECT ON TABLE dodois_rs_monthly TO authenticated;
