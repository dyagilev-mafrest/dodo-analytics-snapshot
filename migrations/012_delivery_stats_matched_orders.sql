-- delivery_stats.orders_with_handover_data: count of delivery orders that had a
-- matching production/orders-handover-time record (has cookingTime).
--
-- Bug found comparing orders_over_40min against a real Dodo IS dashboard
-- screenshot: ~10% of delivery orders (couriers-orders) have NO matching
-- handover-time record at all (confirmed absent under any sales channel, not a
-- join bug), and the fallback formula used for those orders
-- (orderAssemblyAvgTime + heatedShelfTime + deliveryTime) omits cookingTime
-- entirely — cookingTime averages ~11 minutes, so unmatched orders were
-- being scored as "over 40 min" only 13.8% of the time vs 25.2% for matched
-- orders with the accurate formula (Dodo IS reference: 25.7%). There's no
-- reliable substitute for the missing cookingTime, so get_delivery.py now
-- only classifies matched orders into orders_over_40min, and this column
-- tracks that population so the dashboard can use the correct denominator
-- (orders_with_handover_data, not delivery_orders/orders_1in1+2in1+3in1).

ALTER TABLE delivery_stats
    ADD COLUMN IF NOT EXISTS orders_with_handover_data integer NOT NULL DEFAULT 0;
