-- dodois_discount_share_monthly: доля ЗАКАЗОВ со скидкой, по типу и подкатегории
-- акции. Ручной ежемесячный экспорт из Dodo IS Superset, дашборд «Discount
-- Analytics. Share of Orders with discount by Type and subcategory in dynamic».
--
-- ЭТО НЕ ДИСКОНТ В ПРОЦЕНТАХ ОТ ВЫРУЧКИ. Здесь доля заказов, в которых была
-- скидка данного вида: за август 2026 «Другое, Локальные» = 0,42%, то есть
-- локальная акция была в 0,42% заказов. Рублёвая доля того же дисконта в отчётности
-- считается иначе и здесь не приводится.
-- отвечают на разные вопросы: «в скольких заказах» против «сколько денег».
-- Складывать, вычитать и подменять одно другим нельзя.
--
-- Почему ручной экспорт, а не API: у Dodo IS нет эндпоинта с разрезом по
-- подкатегориям акций (разбор доступа — см. project-memory по акциям: роль 55
-- закрывает клиентские данные целиком). Тот же временный паттерн, что у
-- dodois_discount_monthly (045) и dodois_rko_monthly (043).
--
-- Хранение длинной формой, а не колонкой на подкатегорию: подкатегории заводит
-- маркетинг, их набор меняется от выгрузки к выгрузке («CVM, Не операционный»
-- появилась только с февраля), и каждая новая означала бы миграцию.
CREATE TABLE IF NOT EXISTS dodois_discount_share_monthly (
    month          date NOT NULL,   -- первое число месяца
    discount_type  text NOT NULL,   -- «CVM», «Додокоины», «Другое», «Комбо»
    subcategory    text NOT NULL,   -- «Локальные», «Федеральные», «Флеш», …
    -- Доля 0-1, как отдаёт экспорт: 0.0042 = 0,42% заказов. Не умножаем на 100 —
    -- та же договорённость, что в dodois_discount_monthly (045).
    order_share    numeric NOT NULL,
    loaded_at      timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, discount_type, subcategory)
);

CREATE INDEX IF NOT EXISTS idx_discount_share_month
    ON dodois_discount_share_monthly (month);

ALTER TABLE dodois_discount_share_monthly ENABLE ROW LEVEL SECURITY;

-- Обе роли обязательны: anon читает публично, authenticated — залогиненные в
-- дашборд. Без второй политики они получают 403 (грабли, описанные в agents.md).
CREATE POLICY anon_read ON dodois_discount_share_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_discount_share_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_discount_share_monthly TO anon;
GRANT SELECT ON TABLE dodois_discount_share_monthly TO authenticated;
