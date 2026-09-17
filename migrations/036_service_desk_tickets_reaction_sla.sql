-- Данные для гейджей "Скорость реакции" / "Скорость выполнения" (как в
-- нативном дашборде DodoDesk). NAUMEN serviceCall хранит их отдельно от
-- общего complianceSC:
--   reactTime        — целевое время реакции (SLA), {length, interval}
--   reactionTime      — фактическое: {status: STOPED/RUNNING, elapsed мс}
--   reactOverdue      — живой счётчик просрочки реакции: elapsedFromOverdue=0,
--                        пока реакция уложилась в SLA (даже после того, как
--                        реакция уже случилась) — это и есть "соблюдено/нет"
-- resolutionTime уже используется через compliance_code/compliance_title
-- (существующие колонки) — это и есть "скорость выполнения".
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS react_time_length integer;
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS react_time_interval text;
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS reaction_status text;
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS reaction_elapsed_ms bigint;
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS react_overdue_elapsed_ms bigint;
