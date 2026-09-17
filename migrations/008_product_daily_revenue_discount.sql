-- Extend product_daily_revenue with gross revenue (pre-discount) and a channel
-- dimension, needed for the pulse-week "Дисконт" metric (network/channel/unit
-- breakdown of discount % = (gross_revenue - revenue) / gross_revenue).
--
-- Existing rows (aggregated across all channels, per (date, unit_id, product_id))
-- get sales_channel = 'ALL' via the column default, so they keep satisfying the
-- widened primary key without needing a channel value that was never captured.
-- get_product_sales.py's backfill for the trailing 8 weeks replaces those 'ALL'
-- rows with real per-channel rows (see the DELETE step in that backfill run) —
-- older rows stay as single 'ALL' rows, which is fine: compute_lost_revenue.py's
-- lookback (3 preceding same-weekday occurrences) never reaches past ~3 weeks.

ALTER TABLE product_daily_revenue
    ADD COLUMN IF NOT EXISTS gross_revenue numeric NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sales_channel text NOT NULL DEFAULT 'ALL';

ALTER TABLE product_daily_revenue DROP CONSTRAINT IF EXISTS product_daily_revenue_pkey;
ALTER TABLE product_daily_revenue
    ADD PRIMARY KEY (date, unit_id, product_id, sales_channel);
