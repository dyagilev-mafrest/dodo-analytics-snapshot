-- Retrospective revenue forecast baseline — "what would we have expected for this
-- day", used to compare against actual on the dashboard's Revenue/Сеть chart.
-- Reverse-engineered empirically against the third-party "Автографик" forecasting
-- tool (see project memory) — a plain historical average badly overshoots around
-- holiday weeks (Автографик itself claims to smooth for seasonality/holidays), so
-- this excludes known RU holiday windows from the lookback and additionally trims
-- statistical outliers as a safety net for anything not on the hardcoded calendar.
-- See compute_revenue_forecast.py for the exact algorithm. Pure derived table —
-- computed entirely from daily_sales already in Supabase, no Dodo IS API calls.

CREATE TABLE IF NOT EXISTS revenue_forecast_daily (
    date              date    NOT NULL,
    unit_id           text    NOT NULL,
    forecast_revenue  numeric NOT NULL,
    PRIMARY KEY (date, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_revenue_forecast_daily_date
    ON revenue_forecast_daily (date);

ALTER TABLE revenue_forecast_daily ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON revenue_forecast_daily
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE revenue_forecast_daily TO anon;
