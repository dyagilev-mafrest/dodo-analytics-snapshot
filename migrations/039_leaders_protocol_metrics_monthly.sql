-- Главные метрики компании из ЕЖЕМЕСЯЧНОГО протокола совета руководителей
-- ("Протокол совета руководителей Додо Якутск по итогу месяца", отдельная
-- Google-таблица от еженедельного протокола — см. leaders_protocol_metrics
-- в 038_leaders_protocol_metrics.sql). Один лист на встречу; последние
-- вкладки названы просто "YYYY-MM" (раньше — полной датой встречи).
-- См. get_leaders_protocol_monthly.py — тянем самый свежий ЗАПОЛНЕННЫЙ лист
-- (пропускаем ещё не заполненные шаблоны — определяются как побайтовый
-- дубликат цифр предыдущего листа).
CREATE TABLE IF NOT EXISTS leaders_protocol_metrics_monthly (
    report_period     text NOT NULL,      -- имя вкладки-источника, напр. "2026-07"
    metric_label      text NOT NULL,      -- напр. "Выручка Июль  2026"
    plan_month        numeric,            -- план на отчётный месяц ("план предыдущего месяца" в таблице)
    fact_month        numeric,            -- факт за отчётный месяц
    plan_next_month   numeric,            -- план на следующий месяц (прогноз вперёд)
    plan_cumulative   numeric,            -- план накопительно с начала года
    fact_cumulative   numeric,            -- факт накопительно с начала года
    pct_cumulative    numeric,            -- % выполнения накопительного (годового) плана
    plan_monthly      numeric,            -- план (месячный) — дублирует plan_month в источнике, оставлено отдельно на случай расхождения
    fact_monthly      numeric,            -- факт (месячный) — дублирует fact_month в источнике
    pct_monthly       numeric,            -- % выполнения месячного плана
    loaded_at         timestamp NOT NULL,
    PRIMARY KEY (report_period, metric_label)
);

ALTER TABLE leaders_protocol_metrics_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON leaders_protocol_metrics_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON leaders_protocol_metrics_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE leaders_protocol_metrics_monthly TO anon;
GRANT SELECT ON TABLE leaders_protocol_metrics_monthly TO authenticated;
