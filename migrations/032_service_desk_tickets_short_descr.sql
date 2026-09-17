-- Добавляет описание заявки DodoDesk (NAUMEN serviceCall.shortDescr) —
-- title всегда общий формат "REQ-<number>", реальный текст обращения
-- (например, "Провести розетку в зоне кассы") лежит в отдельном атрибуте.
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS short_descr text;
