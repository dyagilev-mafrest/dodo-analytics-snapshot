-- customer_feedback: сырые отзывы клиентов с эвристической классификацией проблемы.
-- Source: Dodo IS API  dodopizza/customer-feedback/recent-feedbacks (unitId, orderId,
--         orderRate, feedbackComment) — не содержит канал (доставка/ресторан), поэтому
--         get_quality.py джойнит его с accounting/sales по orderId, чтобы взять
--         salesChannel.
-- problem_category — эвристическая классификация по ключевым словам в комментарии,
--         аналогично classify_discount() в get_product_sales.py: точного справочника
--         причин у Dodo IS в API нет, это приближение (см. classify_problem() в
--         get_quality.py), заполняется только для order_rate <= 3.
-- Питает блок "Клиентский опыт" в pulse-week: таблицы "ТОП проблем" по каналам.

CREATE TABLE IF NOT EXISTS customer_feedback (
    order_id             text PRIMARY KEY,
    unit_id              text NOT NULL,
    date                 date NOT NULL,
    order_rate           integer NOT NULL,
    feedback_comment     text,
    sales_channel        text,
    problem_category     text,
    order_created_at     timestamp,
    feedback_created_at  timestamp
);

CREATE INDEX IF NOT EXISTS idx_customer_feedback_unit_date
    ON customer_feedback (unit_id, date);

CREATE INDEX IF NOT EXISTS idx_customer_feedback_problem_category
    ON customer_feedback (problem_category) WHERE problem_category IS NOT NULL;

ALTER TABLE customer_feedback ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON customer_feedback
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE customer_feedback TO anon;
