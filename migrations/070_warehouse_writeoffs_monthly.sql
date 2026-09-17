-- Списания ТМЦ по сети помесячно — лист «Списание ТМЦ» той же Google-таблицы
-- «Движение ТМЦ», что и остатки (см. migrations/069).
--
-- Лист — журнал событий: строка на акт списания, с 2023 года их всего полторы
-- сотни. Складываем до «месяц × склад × причина»: сумма по месяцу идёт в отчёт,
-- а причина («Вышел срок» против «Брак при транспортировке») — это ровно тот
-- разрез, который на совете спросят первым.
--
-- ⚠️ МЕСЯЦ БЕЗ СТРОК — ЭТО НОЛЬ, А НЕ ПРОБЕЛ. Списания случаются не каждый
-- месяц: в 2026 их не было в апреле и августе. Отличить «ноль» от «ещё не
-- внесли» можно только по границе журнала, поэтому читатель обязан сравнивать
-- месяц с максимальным в таблице, а не искать строку.
CREATE TABLE IF NOT EXISTS warehouse_writeoffs_monthly (
    month        date NOT NULL,   -- первое число месяца
    warehouse    text NOT NULL,   -- «Мороз», «Сухой», «Холод»
    reason       text NOT NULL,   -- комментарий акта: «Вышел срок», «Брак при транспортировке»
    writeoff_rub numeric NOT NULL,
    acts_count   int NOT NULL,    -- сколько строк журнала сложилось в сумму
    loaded_at    timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, warehouse, reason)
);

CREATE INDEX IF NOT EXISTS idx_warehouse_writeoffs_monthly_month
    ON warehouse_writeoffs_monthly (month);

ALTER TABLE warehouse_writeoffs_monthly ENABLE ROW LEVEL SECURITY;

-- Обе роли обязательны: anon читает публично, authenticated — залогиненные в
-- дашборд. Без второй политики они получают 403 (грабли, описанные в agents.md).
CREATE POLICY anon_read ON warehouse_writeoffs_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON warehouse_writeoffs_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE warehouse_writeoffs_monthly TO anon;
GRANT SELECT ON TABLE warehouse_writeoffs_monthly TO authenticated;
