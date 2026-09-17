-- Реестр отгрузок (грузы от поставщиков до складов/пиццерий МАФРЕСТ), из листа
-- "Реестр отгрузок ОС" таблицы "Реестр отгрузок" (ведёт транспортный логист).
-- Источник для блока «Склад и логистика» — таблица «Отгрузки в пути»
-- (запрошено пользователем 2026-08-12).
--
-- Источник: Google Sheets API (тот же сервисный аккаунт, см. get_warehouse.py).
-- Полный TRUNCATE + INSERT при каждом запуске — нет надёжного естественного
-- ключа (номер счета/УПД не уникален и может повторяться), лист небольшой
-- (~750 строк), полностью перечитывается целиком каждый раз, как и
-- warehouse_stock/warehouse_receipts (migrations/028).
--
-- Не все колонки листа взяты — стоимостные/бухгалтерские поля (накладные,
-- стоимость забора/перевозки/грузчиков, % перевозки, "забит в 1С") вне
-- скоупа дашборда сейчас, можно добрать позже при необходимости.
CREATE TABLE IF NOT EXISTS warehouse_shipments (
    status                  text,    -- "Статус груза": План / Отгружен в ТК / В пути / Задерживается / Прибыл в пункт / Получен / Утерян / Отменен
    planned_receipt_date    date,
    shipped_date            date,    -- "Дата отгрузки в ТК"
    actual_receipt_date     date,    -- "Дата поступления груза"
    days_in_transit         numeric,
    sender                  text,
    receiver                text,
    order_number            text,    -- "Номер счета/УПД/заказа"
    order_amount_rub        numeric,
    payment_status          text,
    departure_city          text,
    carrier                 text,    -- "ТК"
    shipping_to_carrier     text,    -- "Отгрузка в ТК" (силами отправителя/получателя)
    shipping_method         text,    -- "Способ отправки"
    receiving_method        text,    -- "Способ получения"
    temperature_mode        text,    -- 'Мороз' / 'Сухой' / 'Холод'
    destination_unit        text,    -- "Доставить в подразделение"
    weight_kg                numeric,
    volume_m3                numeric,
    package_count            numeric,
    pallet_count             numeric,
    comment                  text
);

CREATE INDEX IF NOT EXISTS idx_warehouse_shipments_status
    ON warehouse_shipments (status);

ALTER TABLE warehouse_shipments ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON warehouse_shipments FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON warehouse_shipments FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE warehouse_shipments TO anon;
GRANT SELECT ON TABLE warehouse_shipments TO authenticated;
