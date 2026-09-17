-- Ответственный сотрудник за заявку (NAUMEN serviceCall.responsible.title,
-- например "Сергей Бутов") — отличается от responsible_team_title
-- (команда/подрядчик-организация, добавлена в миграции 033).
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS responsible_title text;
