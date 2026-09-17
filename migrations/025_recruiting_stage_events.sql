-- Точная понедельная динамика воронки рекрутинга (запрошено пользователем
-- 2026-08-06, grilling-сессия) — дополняет recruiting_deals, который хранит
-- только ТЕКУЩУЮ стадию сделки (снепшот), а значит график "по неделям"
-- на нём — это когорта ("из тех, кто зашёл на неделе X, сколько сейчас в
-- каждом исходе"), а не факт "сколько событий произошло на неделе X".
--
-- Источник: crm.stagehistory.list (entityTypeId=2, filter[@OWNER_ID][]=...
-- батчами по deal_id из recruiting_deals). Одна строка = один реальный
-- переход сделки в стадию, с точным CREATED_TIME — событие, не снепшот.
-- Полный бэкфилл по всем сделкам сразу (решение пользователя — иначе график
-- "сшит" из двух методологий на стыке старых/новых данных).
--
-- STAGE_ID декодируется в человекочитаемое имя через тот же stage_map
-- (crm.dealcategory.stage.list), что и в recruiting_deals — get_recruiting.py
-- и get_recruiting_stage_history.py должны использовать одинаковый маппинг.
CREATE TABLE IF NOT EXISTS recruiting_stage_events (
    event_id     bigint PRIMARY KEY,   -- ID записи истории в Bitrix24
    deal_id      bigint NOT NULL,      -- recruiting_deals.deal_id
    stage_id     text NOT NULL,        -- напр. 'C2:UC_NOWQUB'
    stage_name   text,                 -- напр. '02. Недозвон'
    created_time timestamptz NOT NULL  -- когда сделка вошла в эту стадию
);

CREATE INDEX IF NOT EXISTS idx_recruiting_stage_events_deal
    ON recruiting_stage_events (deal_id);
CREATE INDEX IF NOT EXISTS idx_recruiting_stage_events_created
    ON recruiting_stage_events (created_time);

ALTER TABLE recruiting_stage_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON recruiting_stage_events
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE recruiting_stage_events TO anon;
