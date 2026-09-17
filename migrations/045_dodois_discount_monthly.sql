-- Дисконт (сеть/доставка/ресторан) — ручной ежемесячный экспорт из Dodo IS
-- Superset ("Discount Analytics. Dynamic of discount"), временная замена
-- сетевого/канального разреза нашего расчёта (usePulseWeekDiscountData) в
-- блоке "Продажи" раздела "Месяц" — см. dodois_rko_monthly (043) для того же
-- временного-override рационала. Разбивка по пиццериям НЕ подменяется —
-- в этом экспорте её нет, "По точкам" остаётся на нашем расчёте.
-- Только текущий год — таблица держится крошечной (лимит Supabase 500 МБ).
CREATE TABLE IF NOT EXISTS dodois_discount_monthly (
    month                  date PRIMARY KEY,  -- первое число месяца
    -- Хранится как десятичная дробь (0-1), как отдаёт сам экспорт Dodo IS —
    -- НЕ умножено на 100, в отличие от других dodois_*_monthly таблиц.
    -- Название колонки "_pct" оставлено для единообразия, но это доля, не %.
    -- Фронтенд умножает на 100 при отображении, см. useDodoIsDiscountMonthly.ts.
    delivery_discount_pct  numeric,           -- "Доставка", доля от выручки (0-1)
    restaurant_discount_pct numeric,          -- "Ресторан", доля от выручки (0-1)
    overall_discount_pct   numeric,           -- "Доля дисконта от выручки" (сеть) (0-1)
    loaded_at              timestamp NOT NULL DEFAULT now()
);

ALTER TABLE dodois_discount_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_discount_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_discount_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_discount_monthly TO anon;
GRANT SELECT ON TABLE dodois_discount_monthly TO authenticated;
