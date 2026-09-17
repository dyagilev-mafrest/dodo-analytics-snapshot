-- Подрядчик, ответственный за заявку (NAUMEN serviceCall.responsibleTeam.title,
-- например "ИП Лысенко Москва", "Доктор Холода Розница Москва").
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS responsible_team_title text;
