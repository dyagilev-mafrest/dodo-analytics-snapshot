-- "Объект" — полный адрес точки (NAUMEN serviceCall.location.title, например
-- "Якутск, Лермонтова, 121А, "), детальнее чем client_ou_title (тот даёт
-- только "Якутск-1".."Якутск-7" — здесь бывают ещё под-объекты вроде
-- "Технический отдел"/HR/Бухгалтерия по одному адресу).
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS location_title text;
