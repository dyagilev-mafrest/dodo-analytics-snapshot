-- Списания ТМЦ на центральных складах (Мороз/Сухой/Холод), из листа
-- "Списание ТМЦ" таблицы "Движение ТМЦ" (тот же документ, что
-- warehouse_stock/warehouse_receipts, см. migrations/028). Источник для
-- блока «Склад и логистика» — таблица «Списания ТМЦ» (запрошено
-- пользователем 2026-08-12).
--
-- Источник: Google Sheets API (тот же сервисный аккаунт, см. get_warehouse.py).
-- Небольшой лист (~160 строк на 2026-08-12, полная история с 2023) — полный
-- TRUNCATE + INSERT при каждом запуске, как остальные листы этого документа.
CREATE TABLE IF NOT EXISTS warehouse_writeoffs (
    date           date NOT NULL,
    warehouse      text NOT NULL,  -- 'Мороз' / 'Сухой' / 'Холод'
    nomenclature   text NOT NULL,
    batch          text,           -- "Серия" — партия/срок годности как строка из 1С
    comment        text,           -- причина списания ("Вышел срок", "Брак при транспортировке" и т.п.)
    quantity       numeric,
    price          numeric,
    amount_rub     numeric
);

CREATE INDEX IF NOT EXISTS idx_warehouse_writeoffs_date_wh
    ON warehouse_writeoffs (date, warehouse);

ALTER TABLE warehouse_writeoffs ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON warehouse_writeoffs FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON warehouse_writeoffs FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE warehouse_writeoffs TO anon;
GRANT SELECT ON TABLE warehouse_writeoffs TO authenticated;
