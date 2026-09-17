-- План/факт по штату из Google-таблицы найма (лист "new"), которую еженедельно
-- заполняют управляющие пиццерий, а HR строит по ней план рекрутинга.
-- Запрошено пользователем 2026-08-06, grilling-сессия после находки о том, что
-- воронка Bitrix (recruiting_deals/recruiting_stage_events) страдает от
-- пакетных обновлений HR — эта таблица даёт независимую, дополняющую картину
-- "какой сейчас разрыв между целью и фактом по штату", не про кандидатов.
--
-- Источник: Google Sheets API (сервисный аккаунт, см. get_staffing_plan.py),
-- лист "new". Структура листа — вертикально повторяющийся блок на каждую
-- неделю (строка-заголовок с диапазоном дат, затем ~9-10 строк-пиццерий).
-- Раскладываем широкую таблицу (4 позиции × 4 метрики в колонках) в длинный
-- формат: одна строка = неделя × пиццерия × позиция.
--
-- В скоуп включены ВСЕ юниты из листа, не только 7 пиццерий МАФРЕСТ — ПРЦ и МД
-- тоже МАФРЕСТ (это производство), Якутск-10 — строящаяся пиццерия (решение
-- пользователя 2026-08-06, в отличие от recruiting_deals, где эти же юниты
-- считались "чужими" — это было ошибочное предположение, см. память).
--
-- Стажёры/Медосмотры (колонки U-AA листа) — вне скоупа в этой итерации.
-- Ошибки формул (#DIV/0!, #REF!) и пустые ячейки в исходнике -> NULL, не 0
-- (см. get_staffing_plan.py) — не путаем "нет данных" с фактическим нулём.
CREATE TABLE IF NOT EXISTS staffing_plan (
    week_start           date NOT NULL,
    unit_name            text NOT NULL,
    position             text NOT NULL,  -- 'Пиццамейкер' / 'Кассир' / 'Клинер' / 'Курьер'
    target_headcount     integer,        -- цель команды
    current_headcount    integer,        -- текущий штат
    forecast_departures  integer,        -- прогноз увольнений
    hiring_target        integer,        -- цель найма
    PRIMARY KEY (week_start, unit_name, position)
);

CREATE INDEX IF NOT EXISTS idx_staffing_plan_week
    ON staffing_plan (week_start);

ALTER TABLE staffing_plan ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read ON staffing_plan
    FOR SELECT TO anon USING (true);

GRANT SELECT ON TABLE staffing_plan TO anon;
