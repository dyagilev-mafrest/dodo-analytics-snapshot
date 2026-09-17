-- daily_revenue_by_channel: выручка (net/gross) по unit+каналу продаж за день —
-- предагрегированный аналог product_daily_revenue для фронтенда.
--
-- product_daily_revenue хранит построчно КАЖДЫЙ товар (~2900 строк/день на сеть) —
-- это нужно только compute_lost_revenue.py (среднее по 3 предыдущим таким же дням
-- недели для пары unit_id+product_id). Блок "Дисконт" в pulse-week же читал ровно
-- ту же таблицу ради net/gross выручки по unit+каналу — из-за лимита PostgREST
-- в 1000 строк на запрос это требовало сотен последовательных постраничных
-- запросов на окно в несколько месяцев (см. issue про медленную загрузку
-- блока "Маркетинг", 2026-07-29). Эта таблица даёт тот же результат
-- (SUM revenue/gross_revenue по product_id) на ~20-30 строк/день.

CREATE TABLE IF NOT EXISTS daily_revenue_by_channel (
    date            date    NOT NULL,
    unit_id         text    NOT NULL,
    sales_channel   text    NOT NULL,
    revenue         numeric NOT NULL DEFAULT 0,
    gross_revenue   numeric NOT NULL DEFAULT 0,
    PRIMARY KEY (date, unit_id, sales_channel)
);

CREATE INDEX IF NOT EXISTS idx_daily_revenue_by_channel_unit_date
    ON daily_revenue_by_channel (unit_id, date);

ALTER TABLE daily_revenue_by_channel ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON daily_revenue_by_channel
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE daily_revenue_by_channel TO anon;

-- Разовый бэкфилл из уже загруженной истории product_daily_revenue —
-- без повторных вызовов к Dodo IS API.
INSERT INTO daily_revenue_by_channel (date, unit_id, sales_channel, revenue, gross_revenue)
SELECT date, unit_id, sales_channel, SUM(revenue), SUM(gross_revenue)
FROM product_daily_revenue
GROUP BY date, unit_id, sales_channel
ON CONFLICT (date, unit_id, sales_channel) DO UPDATE SET
    revenue       = EXCLUDED.revenue,
    gross_revenue = EXCLUDED.gross_revenue;
