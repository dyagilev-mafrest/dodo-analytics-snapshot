-- Weekly revenue plan from leadership, network-wide (не по юнитам — руководство
-- даёт план по сети "Якутск" целиком). Точнее месячного плана
-- (leadership_revenue_plan) — там, где недельные данные есть, они перекрывают
-- месячную раскладку; месячный план остаётся фолбэком для недель за пределами
-- этой таблицы (см. apply_leadership_weekly_plan.py — применяется ПОСЛЕ
-- apply_leadership_revenue_plan.py и переписывает те же дни точнее).

CREATE TABLE IF NOT EXISTS leadership_revenue_plan_weekly (
    week_start               date    NOT NULL,  -- понедельник недели
    plan_revenue              numeric NOT NULL,
    leadership_actual_revenue numeric,           -- их собственный "факт" — для сверки, не используется в расчёте
    PRIMARY KEY (week_start)
);

ALTER TABLE leadership_revenue_plan_weekly ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON leadership_revenue_plan_weekly
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE leadership_revenue_plan_weekly TO anon;
