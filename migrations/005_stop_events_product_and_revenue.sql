-- stop_events: добавляем event_type='product' (production/stop-sales-products)
-- и поля для расчёта упущенной выручки.
-- entity_id: productId / ingredientId (null для channel/sector, как и раньше)
-- lost_revenue: заполняется отдельным скриптом compute_lost_revenue.py,
--               только для закрытых стопов (ended_at_local IS NOT NULL)

ALTER TABLE stop_events ADD COLUMN IF NOT EXISTS entity_id text;
ALTER TABLE stop_events ADD COLUMN IF NOT EXISTS lost_revenue numeric;

CREATE INDEX IF NOT EXISTS idx_stop_events_entity
    ON stop_events (entity_id);
