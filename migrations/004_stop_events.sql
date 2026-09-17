-- stop_events: ingredient / channel / sector stop-sale events per unit
-- Source: production/stop-sales-ingredients, production/stop-sales-channels,
--         delivery/stop-sales-sectors
-- One row per stop event; upsert on id (API-provided UUID).
-- Product stops (~1300/day) are intentionally excluded (derived from ingredients).

CREATE TABLE IF NOT EXISTS stop_events (
    id               text PRIMARY KEY,
    event_type       text NOT NULL,    -- 'ingredient' | 'channel' | 'sector'
    unit_id          text NOT NULL,
    unit_name        text,
    entity_name      text,             -- ingredient name / channel name / sector name
    entity_category  text,             -- ingredientCategoryName / channelStopType / 'sector'|'subsector'
    reason           text,
    started_at_local timestamp NOT NULL,
    ended_at_local   timestamp,
    duration_minutes real              -- null while stop is still active
);

CREATE INDEX IF NOT EXISTS idx_stop_events_unit_date
    ON stop_events (unit_id, started_at_local);

CREATE INDEX IF NOT EXISTS idx_stop_events_type_date
    ON stop_events (event_type, started_at_local);

ALTER TABLE stop_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON stop_events
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE stop_events TO anon;
