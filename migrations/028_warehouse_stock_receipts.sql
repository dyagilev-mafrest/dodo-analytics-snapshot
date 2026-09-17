-- Остатки и поступления ТМЦ на центральных складах (Мороз/Сухой/Холод), из
-- Google-таблицы "Движение ТМЦ" ("Остатки ТМЦ" и "Поступления ТМЦ"), которую
-- ведёт отдел закупок. Источник для блока «Склад и логистика» (запрошено
-- пользователем 2026-08-12).
--
-- Источник: Google Sheets API (сервисный аккаунт dodo-analytics-sheets-reader,
-- см. get_warehouse.py) — тот же паттерн, что staffing_plan/hr_hiring_funnel.
--
-- Обе таблицы полностью перезаливаются при каждом запуске (TRUNCATE + INSERT):
-- лист "Остатки ТМЦ" — еженедельный снепшот остатков по партиям (не история
-- изменений, а полный срез на дату), лист "Поступления ТМЦ" — журнал приходов.
-- Оба листа отдают из 1С полную историю целиком при каждом чтении, поэтому
-- upsert по естественному ключу не нужен и не надёжен (партии/номенклатура не
-- гарантированно уникальны в пределах даты+склада).
CREATE TABLE IF NOT EXISTS warehouse_stock (
    date                 date NOT NULL,
    warehouse            text NOT NULL,  -- 'Мороз' / 'Сухой' / 'Холод'
    nomenclature         text NOT NULL,
    nomenclature_group   text,
    batch                text,           -- "Серия ЦО" — партия/срок годности как строка из 1С
    unit                 text,
    quantity             numeric,
    value_rub            numeric
);

CREATE INDEX IF NOT EXISTS idx_warehouse_stock_date_wh
    ON warehouse_stock (date, warehouse);

ALTER TABLE warehouse_stock ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON warehouse_stock FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON warehouse_stock FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE warehouse_stock TO anon;
GRANT SELECT ON TABLE warehouse_stock TO authenticated;

CREATE TABLE IF NOT EXISTS warehouse_receipts (
    date                 date NOT NULL,
    warehouse            text NOT NULL,
    supplier             text,
    nomenclature         text NOT NULL,
    nomenclature_group   text,
    unit                 text,
    quantity             numeric,
    value_rub            numeric
);

CREATE INDEX IF NOT EXISTS idx_warehouse_receipts_date_wh
    ON warehouse_receipts (date, warehouse);

ALTER TABLE warehouse_receipts ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON warehouse_receipts FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON warehouse_receipts FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE warehouse_receipts TO anon;
GRANT SELECT ON TABLE warehouse_receipts TO authenticated;
