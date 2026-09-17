-- leadership_revenue_plan_network: сетевой план выручки по месяцам — строка
-- «1. Выручка» листа «Финмодель 2026» той же таблицы «План выручки на 2026 -
-- Шах», что питает leadership_revenue_plan.
--
-- ЗАЧЕМ ОТДЕЛЬНАЯ ТАБЛИЦА, А НЕ СУММА ПО ЮНИТАМ
--
-- С июля 2026 в плане заложен переезд Якутска-5 в ТЦ БУМ: Якутску-5 назначено
-- 0, а появляются «Якутск 8 (202 мкрн)» и «Якутск 9 (ТЦ БУМ)». Переезд не
-- состоялся, ни одна из двух точек не открылась, Якутск-5 работает. У этих
-- строк нет unit_id в Dodo IS, поэтому в leadership_revenue_plan они попасть не
-- могут — и сумма по юнитам за июль-декабрь оказывается меньше утверждённого
-- плана: 126 455 028 против 159 467 552 за август.
--
-- Решение руководства (16.09.2026): план считается по строке «1. Выручка»
-- целиком. План утверждён в первом квартале 2026 и с тех пор не
-- корректировался — разбивка по заведениям внутри него уже не отражает сеть, но
-- обещание совета дано по итоговой цифре, и отчёт судит по ней.
--
-- Поэтому две таблицы живут порознь и не противоречат друг другу:
--   * leadership_revenue_plan — план по ДЕЙСТВУЮЩИМ пиццериям. Его читает
--     apply_leadership_revenue_plan.py, пересчитывая суточный прогноз внутри
--     месяца; юниту без выручки там пересчитывать нечего.
--   * этот файл — сетевое обещание. Его читает месячный отчёт
--     («Выполнение плана по выручке»).
--
-- Сумма по юнитам НЕ равна сетевому плану с июля 2026 — это не рассинхрон, а
-- зафиксированное состояние финмодели. Не «сводите» их обратно.
CREATE TABLE IF NOT EXISTS leadership_revenue_plan_network (
    month         date    NOT NULL,  -- первое число месяца
    plan_revenue  numeric NOT NULL,
    loaded_at     timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month)
);

ALTER TABLE leadership_revenue_plan_network ENABLE ROW LEVEL SECURITY;

-- Обе роли обязательны: anon читает публично, authenticated — залогиненные в
-- дашборд. Без второй политики они получают 403 (грабли, описанные в agents.md).
CREATE POLICY anon_read ON leadership_revenue_plan_network FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON leadership_revenue_plan_network FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE leadership_revenue_plan_network TO anon;
GRANT SELECT ON TABLE leadership_revenue_plan_network TO authenticated;
