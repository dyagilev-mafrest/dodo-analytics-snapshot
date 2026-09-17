-- restaurant_stats.tracking_pending_sum: sum of trackingPendingTime (seconds,
-- production/orders-handover-time) for the same order population as
-- cooking_time_sum (Dine-in + Takeaway, cookingTime > 0). Official Dodo IS
-- methodology: время приготовления в ресторане = ожидание (trackingPendingTime)
-- + приготовление + сборка (cookingTime already folds in assembly), so
-- (cooking_time_sum + tracking_pending_sum) / dine_in_orders is the full
-- order-to-handover cycle — previously cooking_time_sum alone silently
-- omitted the tracker-wait stage.

ALTER TABLE restaurant_stats
    ADD COLUMN IF NOT EXISTS tracking_pending_sum real DEFAULT 0;
