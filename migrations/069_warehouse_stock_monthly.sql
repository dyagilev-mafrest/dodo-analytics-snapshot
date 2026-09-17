-- Остатки ТМЦ по сети на конец месяца — лист «Остатки ТМЦ месяц» Google-таблицы
-- «Движение ТМЦ» (1GXCWU8z1cSqCpPzawQCXAUUFLxvkoZegZQVTh1mvpjQ). Питает строку
-- «Остатки товарного запаса» месячного отчёта.
--
-- Почему не из warehouse_stock (028-030): та таблица — недельные снимки из Dodo
-- IS, и первая её дата 16.08.2026, то есть месяца целиком в ней нет ни одного.
-- Лист даёт январь-август 2026 сразу.
--
-- Это не свёртка недельных снимков: за август лист даёт 74 917 918, а ближайшие
-- снимки 23.08 и 30.08 — 75 507 597 и 73 285 436. Цифра считается в 1С на конец
-- месяца отдельно, поэтому и хранится отдельно, а не выводится из warehouse_stock.
--
-- Зерно «месяц × склад × группа»: в листе три склада (Мороз/Сухой/Холод) и
-- десяток групп номенклатуры, и разрез стоит копейки — 8 месяцев дают меньше
-- трёх сотен строк. Сумма по месяцу и есть то, что показывает отчёт.
--
-- Строки с пустой «Оценкой» (нулевой остаток позиции) не грузятся: ноль рублей
-- и отсутствие позиции на складе — одно и то же, а 600 пустых строк в выгрузке
-- только мешали бы читать таблицу.
CREATE TABLE IF NOT EXISTS warehouse_stock_monthly (
    month      date NOT NULL,   -- первое число месяца; остаток на его конец
    warehouse  text NOT NULL,   -- «Мороз», «Сухой», «Холод»
    group_name text NOT NULL,   -- группа номенклатуры: «Продукты», «Упаковка», …
    stock_rub  numeric NOT NULL,
    loaded_at  timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, warehouse, group_name)
);

CREATE INDEX IF NOT EXISTS idx_warehouse_stock_monthly_month
    ON warehouse_stock_monthly (month);

ALTER TABLE warehouse_stock_monthly ENABLE ROW LEVEL SECURITY;

-- Обе роли обязательны: anon читает публично, authenticated — залогиненные в
-- дашборд. Без второй политики они получают 403 (грабли, описанные в agents.md).
CREATE POLICY anon_read ON warehouse_stock_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON warehouse_stock_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE warehouse_stock_monthly TO anon;
GRANT SELECT ON TABLE warehouse_stock_monthly TO authenticated;
