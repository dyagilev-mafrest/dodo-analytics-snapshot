-- Воронка рекрутинга из Bitrix24 CRM (запрошено пользователем 2026-08-04) —
-- отдельный источник от Dodo IS, для будущего блока "Метрики команды" /
-- воронка найма (дополняет staff_headcount_daily, который считает уже
-- принятых сотрудников через Dodo IS, а не сам процесс подбора).
--
-- Источник: сделки CRM (crm.deal.list), воронка (CATEGORY_ID) = 2 "HR".
-- Одна сделка = один кандидат на одну вакансию. TITLE = ФИО кандидата.
-- Загружается ВСЯ история (с 2023-10-23, самой ранней сделки в воронке) —
-- решение пользователя, без фильтра по пиццериям (в поле unit_name кроме
-- наших 7 юнитов встречаются "Якутск-10" и "ПРЦ" — тоже загружаются,
-- разбор вне скоупа наших 7 юнитов оставлен на потом).
--
-- Поля-перечисления (Пиццерии/Позиция/Тип позиции/Источник/Тип поиска)
-- декодированы из числовых ID в текст через crm.deal.userfield.get на
-- этапе ETL (get_recruiting.py) — хранится уже человекочитаемый текст,
-- не сырой Bitrix ID, чтобы не тащить отдельный справочник в дашборд.
CREATE TABLE IF NOT EXISTS recruiting_deals (
    deal_id       bigint PRIMARY KEY,
    candidate_name text,
    stage_id      text NOT NULL,      -- напр. 'C2:UC_NOWQUB'
    stage_name    text,               -- напр. '02. Недозвон'
    unit_name     text,               -- поле "Пиццерии"
    position      text,               -- поле "Позиция" (вакансия)
    position_type text,               -- поле "Тип позиции": Курьер/Кухня/Склад
    source        text,               -- поле "Источник": HH.ru, Avito, ...
    search_type   text,               -- поле "Тип поиска": Отклик/Холодный поиск
    date_create   timestamptz NOT NULL,
    date_modify   timestamptz,
    closedate     timestamptz,
    assigned_by_id text
);

CREATE INDEX IF NOT EXISTS idx_recruiting_deals_date_create
    ON recruiting_deals (date_create);
CREATE INDEX IF NOT EXISTS idx_recruiting_deals_unit
    ON recruiting_deals (unit_name);
CREATE INDEX IF NOT EXISTS idx_recruiting_deals_stage
    ON recruiting_deals (stage_id);

ALTER TABLE recruiting_deals ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON recruiting_deals
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE recruiting_deals TO anon;
