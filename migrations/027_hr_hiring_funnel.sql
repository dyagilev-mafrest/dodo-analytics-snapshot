-- Еженедельная воронка найма кухни из Google-таблицы HR ("HR meet 2026"),
-- запрошено пользователем 2026-08-07 в продолжение блока "Штат и план найма"
-- (see staffing_plan, migration 026) — тот источник даёт цель/факт по штату,
-- этот даёт саму воронку отклика (отклики -> собеседования -> проб.смены),
-- которой нет больше нигде (ни в Bitrix recruiting_deals, ни в staffing_plan).
--
-- Источник: тот же сервисный аккаунт Google (см. get_staffing_plan.py), лист
-- "HR meet 2026" в spreadsheet 1Qm-4sQLD0VKWz3nVDJ8FSA3FnhLbd-5GYkQ4MWQ2xIk.
-- Формат листа: вертикально повторяющийся блок на каждую неделю (7 строк-
-- позиций Пиццамейкеры/Кассиры/Клинеры/Тестомейкеры/Повара МД/Курьеры/Кухня
-- ИТОГО в колонках A-G, плюс отдельная 3-строчная мини-таблица воронки откликов
-- в колонках H-L, НЕ выровненная построчно с позициями — читаем по контенту
-- ("Кухня ИТОГО"/"Курьеры" в колонке B, "Всего откликов в HR"/"Курьеры" в
-- колонке H), не по фиксированному смещению строк (см. get_hr_hiring_funnel.py).
--
-- Воронка откликов (responses_total..trial_shifts) — ТОЛЬКО по кухне
-- (соответствует строке "Всего откликов в HR"). У курьеров отдельная,
-- заметно более бедная воронка без формальных собеседований (там почти
-- всегда стоит "х" вместо числа) — храним только отклики и проб.смены.
--
-- "% выполнения" сознательно не храним — считаем в дашборде на лету
-- (факт / план_предыдущей_недели), т.к. значение в самом листе часто пустое
-- или #DIV/0! (план = 0). "х", "#DIV/0!" и подобные -> NULL, не 0.
CREATE TABLE IF NOT EXISTS hr_hiring_funnel (
    week_start            date PRIMARY KEY,
    kitchen_plan_prev     integer,  -- план найма на эту неделю (столбец "План предыдущей недели")
    kitchen_hired_fact    integer,  -- факт принятых на кухню за неделю
    kitchen_plan_next     integer,  -- план на следующую неделю
    responses_total       integer,  -- "Всего откликов в HR"
    interviews_scheduled  integer,
    interviews_conducted  integer,
    trial_shifts          integer,
    courier_plan_prev     integer,
    courier_hired_fact    integer,
    courier_plan_next     integer,
    courier_responses     integer,
    courier_trial_shifts  integer
);

ALTER TABLE hr_hiring_funnel ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON hr_hiring_funnel
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE hr_hiring_funnel TO anon;
