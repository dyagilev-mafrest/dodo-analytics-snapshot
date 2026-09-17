-- Order-time decomposed by stage, separately per sales channel — from Dodo IS's
-- own pre-aggregated endpoint (production/orders-handover-statistics), not a
-- join we build ourselves. Feeds a stage-by-stage breakdown chart for Доставка
-- (Delivery) and Ресторан (DineIn+TakeAway, combined at query time, weighted by
-- orders_count — same convention already used elsewhere in this project for
-- that channel pairing).
--
-- avg_order_assembly_time is null for Delivery (that stage doesn't apply there)
-- and populated for DineIn — a separate stage from avg_cooking_time, not a
-- duplicate (see get_restaurant.py's note that assemblyTime there is already
-- folded into cookingTime — this is a DIFFERENT field from a DIFFERENT endpoint,
-- don't conflate the two).
--
-- No data before ~2024-01-15 (same underlying source as production/orders-
-- handover-time, restaurant_stats' known floor).

CREATE TABLE IF NOT EXISTS handover_time_stats (
    date                       date    NOT NULL,
    unit_id                    text    NOT NULL,
    sales_channel              text    NOT NULL,  -- 'Delivery' | 'DineIn' | 'TakeAway'
    avg_tracking_pending_time  numeric,            -- seconds
    avg_cooking_time           numeric,
    avg_heated_shelf_time      numeric,
    avg_order_assembly_time    numeric,
    avg_order_handover_time    numeric,
    orders_count               integer NOT NULL DEFAULT 0,
    PRIMARY KEY (date, unit_id, sales_channel)
);

CREATE INDEX IF NOT EXISTS idx_handover_time_stats_unit_date
    ON handover_time_stats (unit_id, date);

ALTER TABLE handover_time_stats ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON handover_time_stats
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE handover_time_stats TO anon;
