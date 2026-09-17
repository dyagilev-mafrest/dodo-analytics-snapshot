-- customer_ratings_daily: рейтинг клиентов (1-5) по доставке/ресторану за день/точку.
-- Source: Dodo IS API  customer-feedback/customer-ratings — отдаёт агрегат
--         (avgDineInOrderRate, avgDeliveryOrderRate, счётчики) за произвольный
--         period, поэтому скрипт get_quality.py запрашивает его отдельно на
--         каждый день, чтобы получить дневной ряд для трендов.
-- Питает блок "Клиентский опыт" в pulse-week: карточки рейтинга + графики динамики.

CREATE TABLE IF NOT EXISTS customer_ratings_daily (
    date                  date    NOT NULL,
    unit_id               text    NOT NULL,
    unit_name             text,
    avg_dine_in_rate      numeric,
    avg_delivery_rate     numeric,
    dine_in_rate_count    integer NOT NULL DEFAULT 0,
    delivery_rate_count   integer NOT NULL DEFAULT 0,
    PRIMARY KEY (date, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_customer_ratings_daily_unit_date
    ON customer_ratings_daily (unit_id, date);

ALTER TABLE customer_ratings_daily ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON customer_ratings_daily
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE customer_ratings_daily TO anon;
