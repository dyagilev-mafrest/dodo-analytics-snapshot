-- Главные метрики компании из еженедельного протокола встречи лидеров
-- (Google Sheets "Протокол встречи лидеров Додо Якутск", вкладка = дата
-- встречи). См. get_leaders_protocol.py — тянем только САМЫЙ СВЕЖИЙ лист
-- при каждом запуске (не историю), таблица растёт по одной строке в неделю
-- на метрику (сейчас только "Выручка").
CREATE TABLE IF NOT EXISTS leaders_protocol_metrics (
    meeting_date     date NOT NULL,     -- дата встречи лидеров (среда), метрики за прошедшую неделю
    metric_label     text NOT NULL,     -- напр. "Выручка - Шах август "
    plan_prev_week   numeric,           -- план на прошедшую (только что закончившуюся) неделю
    fact_week        numeric,           -- факт за эту неделю
    plan_cumulative  numeric,           -- план накопительно с начала месяца
    fact_cumulative  numeric,           -- факт накопительно с начала месяца — это и есть "факт месячный" на сегодня
    pct_cumulative   numeric,           -- % выполнения накопительного плана
    plan_monthly     numeric,           -- план на весь месяц
    loaded_at        timestamp NOT NULL,
    PRIMARY KEY (meeting_date, metric_label)
);

ALTER TABLE leaders_protocol_metrics ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON leaders_protocol_metrics FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON leaders_protocol_metrics FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE leaders_protocol_metrics TO anon;
GRANT SELECT ON TABLE leaders_protocol_metrics TO authenticated;
