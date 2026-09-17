-- Reference table for delivery/delivery-sectors (Dodo IS Public API) — the sector
-- definition endpoint we never used before finding it in the official OpenAPI specs
-- (docs/dodo-is-openapi/dodo-is-api.yaml). Gives a real sectorId per sector, plus
-- isDeleted/isStopped/isSubSector/geometry — none of which stop_events or
-- hourly_revenue_by_sector currently carry (both key sectors by name only).
--
-- Snapshot table, not a history: refreshed by re-running get_delivery_sectors.py,
-- which upserts on (unit_id, sector_id).
create table if not exists delivery_sectors (
    unit_id      text not null,
    sector_id    text not null,
    unit_name    text,
    sector_name  text,
    is_deleted   boolean not null default false,
    is_stopped   boolean not null default false,
    is_subsector boolean not null default false,
    geometry     jsonb,
    updated_at   timestamptz not null default now(),
    primary key (unit_id, sector_id)
);

create index if not exists delivery_sectors_unit_name_idx
    on delivery_sectors (unit_id, sector_name);

-- Дашборд (Supabase JS client, anon/authenticated роли) читает эту таблицу
-- напрямую — без гранта запрос молча зависает на "Загрузка..." (найдено
-- 2026-08-28: dodo-analytics-dashboard's usePulseWeekStopsRevenueData.ts
-- зафетчил её и бесконечно висел, т.к. GRANT забыли добавить при создании).
grant select on delivery_sectors to anon, authenticated;
