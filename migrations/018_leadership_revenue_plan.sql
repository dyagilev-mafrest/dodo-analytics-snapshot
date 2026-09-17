-- Monthly revenue plan from leadership (МАФРЕСТ), one figure per unit per month —
-- manually imported from the shared "План выручки на 2026 - Шах" Google Sheet
-- (no stable public API/export for that sheet, and its layout is a hand-built
-- financial model with merged headers, so this is a manual snapshot, not an
-- automated daily ETL job). Used by apply_leadership_revenue_plan.py to rescale
-- revenue_forecast_daily within each month so it sums exactly to this target,
-- while keeping our own weekday-shape baseline as the within-month distribution.

CREATE TABLE IF NOT EXISTS leadership_revenue_plan (
    unit_id       text NOT NULL,
    month         date NOT NULL,  -- first day of the month
    plan_revenue  numeric NOT NULL,
    PRIMARY KEY (unit_id, month)
);

ALTER TABLE leadership_revenue_plan ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON leadership_revenue_plan
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE leadership_revenue_plan TO anon;

-- Separate column, not an overwrite of forecast_revenue — apply_leadership_revenue_plan.py
-- always rescales FROM the untouched baseline in forecast_revenue, so re-running it is
-- idempotent (rescaling an already-rescaled value against a shifted sum would drift).
-- Dashboard prefers this column when present, falls back to forecast_revenue otherwise.
ALTER TABLE revenue_forecast_daily
    ADD COLUMN IF NOT EXISTS leadership_forecast_revenue numeric;
