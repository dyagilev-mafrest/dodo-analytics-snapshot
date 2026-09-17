-- Почасовая выручка по каналу и по сектору доставки — нужна для расчёта
-- "упущенной выручки" от стопов "Пиццерия"(channel) и "Сектор" по формуле
-- Dodo IS (см. статью "Дашборд Стопы пиццерий и активность секторов доставки"):
--   упущенная_выручка_за_день = время_стопа_в_рабочие_часы
--       × средняя_выручка_в_тот_же_день_недели_и_тот_же_час_за_4_недели
--
-- hourly_revenue_by_channel: из accounting/sales (soldAtLocal уже даёт время
-- заказа, не только дату) — get_product_sales.py агрегирует и туда.
--
-- hourly_revenue_by_sector: sectorId не приходит в accounting/sales, поэтому
-- строится джойном delivery/couriers-orders (sectorId по orderId) с
-- accounting/sales (revenue по orderId) — только Delivery-заказы,
-- ~98% заказов находят пару (тот же порядок недостачи, что и в delivery_stats).
-- Отдельный скрипт get_sector_revenue.py, не завязан на cadence других job'ов.

CREATE TABLE IF NOT EXISTS hourly_revenue_by_channel (
    date            date    NOT NULL,
    hour            smallint NOT NULL,  -- 0-23, локальное время точки
    unit_id         text    NOT NULL,
    sales_channel   text    NOT NULL,
    revenue         numeric NOT NULL DEFAULT 0,
    PRIMARY KEY (date, hour, unit_id, sales_channel)
);

CREATE INDEX IF NOT EXISTS idx_hourly_revenue_by_channel_unit_date
    ON hourly_revenue_by_channel (unit_id, date);

ALTER TABLE hourly_revenue_by_channel ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON hourly_revenue_by_channel
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE hourly_revenue_by_channel TO anon;


CREATE TABLE IF NOT EXISTS hourly_revenue_by_sector (
    date            date    NOT NULL,
    hour            smallint NOT NULL,
    unit_id         text    NOT NULL,
    sector_id       text    NOT NULL,
    sector_name     text,
    revenue         numeric NOT NULL DEFAULT 0,
    orders_count    integer NOT NULL DEFAULT 0,
    PRIMARY KEY (date, hour, unit_id, sector_id)
);

CREATE INDEX IF NOT EXISTS idx_hourly_revenue_by_sector_unit_date
    ON hourly_revenue_by_sector (unit_id, date);

ALTER TABLE hourly_revenue_by_sector ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON hourly_revenue_by_sector
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE hourly_revenue_by_sector TO anon;
