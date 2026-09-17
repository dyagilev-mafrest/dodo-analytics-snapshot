-- Блок "Метрики команды" / "Кухня" (dodo-analytics-dashboard).
--
-- staff_headcount_daily: ежедневный снимок активного штата по юнитам с
-- разбивкой по опыту. Тиры считаются по КУМУЛЯТИВНЫМ отработанным часам на
-- человека за всё время (staff/incentives-by-members, clockIn/clockOutAtLocal),
-- не по стажу в календарных днях и не по названию должности:
--   novice      <  25ч
--   trainee     25–250ч
--   experienced 250ч+
-- Часы считаются с 2016-01-01 (нижняя граница доступной истории API), чтобы
-- сотрудники, пришедшие до глубины бэкфилла (2024-01-15), сразу попадали в
-- верную категорию, а не стартовали как "новички".
--
-- NB (2026-08-04): турновер-таблица (приём/увольнение по категориям)
-- сознательно НЕ создана в этом заходе — staff/positions/history's
-- leavePositionOn/isActive не дают надёжного сигнала реального увольнения
-- (проверено на полной истории всех 408 сотрудников сети: ни у одного
-- хронологически последняя запись не закрыта — leavePositionOn всегда либо
-- отсутствует, либо это перевод/повышение, продолженное следующей записью).
-- См. project_dodo_team_section.md для деталей и следующего шага (нужен
-- scope staffmembers:read к staff/members/{id}).
CREATE TABLE IF NOT EXISTS staff_headcount_daily (
    date              date NOT NULL,
    unit_id           text NOT NULL,
    unit_name         text,
    novice_count      integer NOT NULL DEFAULT 0,
    trainee_count     integer NOT NULL DEFAULT 0,
    experienced_count integer NOT NULL DEFAULT 0,
    active_count      integer NOT NULL DEFAULT 0,
    PRIMARY KEY (date, unit_id)
);

CREATE INDEX IF NOT EXISTS idx_staff_headcount_daily_unit_date
    ON staff_headcount_daily (unit_id, date);

ALTER TABLE staff_headcount_daily ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON staff_headcount_daily
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE staff_headcount_daily TO anon;

-- staff_hours_state: internal running total, not read by the dashboard. Lets
-- the daily job add just that day's new shift hours instead of re-fetching
-- the full multi-year incentives-by-members history on every run. Seeded once
-- by the historical backfill (get_team.py --backfill), then advanced
-- incrementally by the normal daily run.
CREATE TABLE IF NOT EXISTS staff_hours_state (
    staff_id         text NOT NULL PRIMARY KEY,
    hours_asof       date NOT NULL,
    cumulative_hours numeric NOT NULL DEFAULT 0
);
