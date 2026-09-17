-- Заявки DodoDesk (NAUMEN ITSM365, sd/services/rest) — служба поддержки/техобслуживания
-- франчайзи МАФРЕСТ (Якутск). API не поддерживает фильтр по дате/компании в
-- методе find, поэтому весь сервис-деск (все франчайзи по РФ) идёт единым
-- списком, отфильтрованным на стороне ETL по clientOU (см. OUR_OUS в get_service_desk.py).
--
-- sync_state — курсор постраничного обхода find/serviceCall: список растёт
-- только в конец (новые заявки получают следующий по возрастанию offset), так
-- что инкрементальный джоб просто продолжает читать с сохранённого offset,
-- без какого-либо диапазона дат.
CREATE TABLE IF NOT EXISTS service_desk_tickets (
    ticket_uuid       text PRIMARY KEY,
    number            integer,
    title             text NOT NULL,
    state             text NOT NULL,
    mark_code         text,           -- 1..5, null = не оценили
    mark_title        text,
    compliance_code   text,           -- '0'=Просрочен .. '100'=Более 60%
    compliance_title  text,
    priority_code     text,
    priority_title    text,
    service_title     text,
    client_ou_uuid    text NOT NULL,
    client_ou_title   text NOT NULL,
    client_name       text,
    registration_date timestamp NOT NULL,
    date_decision     timestamp,
    loaded_at         timestamp NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_service_desk_tickets_registration
    ON service_desk_tickets (registration_date);
CREATE INDEX IF NOT EXISTS idx_service_desk_tickets_ou
    ON service_desk_tickets (client_ou_uuid);

CREATE TABLE IF NOT EXISTS service_desk_sync_state (
    sync_key    text PRIMARY KEY,     -- 'serviceCall'
    next_offset integer NOT NULL,
    updated_at  timestamp NOT NULL
);

ALTER TABLE service_desk_tickets ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON service_desk_tickets FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON service_desk_tickets FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE service_desk_tickets TO anon;
GRANT SELECT ON TABLE service_desk_tickets TO authenticated;
