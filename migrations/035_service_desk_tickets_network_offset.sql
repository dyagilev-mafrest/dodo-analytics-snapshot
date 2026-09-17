-- Позиция (offset постраничного /find по всей сети DodoDesk), на которой
-- была найдена эта заявка — используется, чтобы точечно перезапрашивать
-- страницы с уже известными ОТКРЫТЫМИ заявками (обновить state/mark/SLA),
-- не пересканируя всю сеть целиком (см. get_service_desk.py::refresh_open_tickets).
ALTER TABLE service_desk_tickets ADD COLUMN IF NOT EXISTS network_offset integer;
CREATE INDEX IF NOT EXISTS idx_service_desk_tickets_network_offset
    ON service_desk_tickets (network_offset);
