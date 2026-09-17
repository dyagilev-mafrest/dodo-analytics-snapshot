-- delivery_stats.orders_over_60min: count of delivery orders (with matching
-- production/orders-handover-time record — same population and denominator as
-- orders_over_40min, i.e. orders_with_handover_data) whose full delivery time
-- (trackingPendingTime + cookingTime + heatedShelfTime + deliveryTime) exceeded
-- 60 minutes. Computed alongside orders_over_40min in get_delivery.py's
-- aggregate_trip_orders() — same formula, higher threshold.

ALTER TABLE delivery_stats
    ADD COLUMN IF NOT EXISTS orders_over_60min integer NOT NULL DEFAULT 0;
