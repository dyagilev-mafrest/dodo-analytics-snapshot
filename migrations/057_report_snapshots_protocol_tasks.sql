-- Отчёт-повестка: замороженный снапшот периода и задачи встречи лидеров.
--
-- Зачем именно снапшот, а не пересчёт на лету: цифры дозревают неделями
-- (незакрытые стопы закрываются задним числом, сходимость по стопам продуктов
-- росла 68% → ~90%, ручные выгрузки Dodo IS приходят с задержкой). Если открыть
-- прошлую неделю заново, увидишь другие числа, чем те, по которым принимали
-- решение, и разговор на встрече уедет в спор о данных вместо действий. Плюс
-- формулировка «худшая неделя за 8» обязана оставаться правдой.
--
-- Храним ПОСЧИТАННЫЙ РЕЗУЛЬТАТ, а не копию данных: цифры, нормативы, вердикты,
-- три списка отклонений. Отчёт — единицы килобайт, ~52 в год. Если сложить срез
-- таблиц, своими руками получим второй product_daily_revenue (271 МБ из 423).

CREATE TABLE IF NOT EXISTS report_snapshots (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start  date NOT NULL,
    period_end    date NOT NULL,  -- включительно
    granularity   text NOT NULL CHECK (granularity IN ('week', 'month')),
    generated_at  timestamptz NOT NULL DEFAULT now(),
    -- кто сформировал: пока вручную после сверки метрик, поэтому важно знать чей
    generated_by  text,
    -- посчитанный отчёт: саммари, три списка, значения с нормативами и вердиктами
    payload       jsonb NOT NULL,
    -- прошла ли проверка полноты данных. false — отчёт показывает баннер вместо
    -- цифр: отчёт по неполным данным хуже отсутствия отчёта, решения примут по
    -- вранью. Проверять надо и ежедневные таблицы, и ручные месячные выгрузки.
    data_complete boolean NOT NULL DEFAULT false,
    notes         text
);

-- Нулевой пункт встречи («что обещали на прошлой неделе») читает последние
-- снапшоты, поэтому сортировка по периоду — основной доступ.
CREATE INDEX IF NOT EXISTS report_snapshots_period_idx
    ON report_snapshots (period_start DESC);

-- Один отчёт на период и гранулярность: перегенерация заменяет, а не плодит.
CREATE UNIQUE INDEX IF NOT EXISTS report_snapshots_period_gran_key
    ON report_snapshots (period_start, period_end, granularity);


-- Задачи встречи. ДВА ВХОДА, ОДИН СПИСОК: из отклонения (с якорем) и
-- заведённая руками, когда отделение приносит своё («инвентаризация в
-- DodoDesk», «проект по сатураторам» — реальные записи из протокола). Поэтому
-- якорные поля nullable. Второй протокол заводить нельзя: если задачу,
-- принесённую отделением, некуда положить, её положат в старую гугл-таблицу.
CREATE TABLE IF NOT EXISTS protocol_tasks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- в каком отчёте задача появилась (null — заведена вне отчёта)
    snapshot_id   uuid REFERENCES report_snapshots (id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    -- ── якорь: то, что делает продукт способным сказать «не сдвинулось» ──
    -- Именно связка решения с (метрика, юнит, период) и есть продукт: ни
    -- гугл-таблица, ни внешний трекер её не дают. Ключ метрики — из реестра
    -- блоков дашборда (blockRegistry.ts).
    metric_key    text,
    unit_id       text,
    period_start  date,
    period_end    date,
    anchor_value  numeric,   -- значение, при котором принимали решение
    anchor_target numeric,   -- норматив на тот момент (из metricTargets)
    anchor_cost   numeric,   -- цена отклонения в ₽ — ТОЛЬКО там, где считается
                             -- честно (стопы, разрыв к плану). Придуманных
                             -- коэффициентов «рейтинг → ₽» быть не должно.

    -- ── исход: обязателен, состояния «висит без ответа» не существует ──
    -- 'taken'    — взято в работу (отделение + владелец + срок)
    -- 'accepted' — «принимаем как есть» + строка почему. Это НЕ провал, а
    --              работающий контур: отказ, произнесённый вслух и записанный,
    --              делает видимым «четвёртый раз подряд принимаем как есть».
    outcome       text NOT NULL CHECK (outcome IN ('taken', 'accepted')),

    -- отделение по протоколу встречи: hr | ko | marketing | fo | os | to | dir
    -- (отделений 5 и 6 не существует). Без CHECK: состав меняется, а реестр
    -- отделений живёт в дашборде.
    department    text,
    owner         text,
    due_date      date,
    -- для 'accepted' — одна строка почему; для 'taken' — необязательное описание
    reason        text,

    status        text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'dropped')),
    status_note   text,

    CONSTRAINT protocol_tasks_period_order CHECK (period_end IS NULL OR period_start IS NULL OR period_end >= period_start),
    -- «принимаем как есть» обязано быть объяснено — иначе исход превращается в
    -- способ отмолчаться
    CONSTRAINT protocol_tasks_accepted_needs_reason CHECK (outcome <> 'accepted' OR reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS protocol_tasks_snapshot_idx ON protocol_tasks (snapshot_id);
-- Нулевой пункт встречи показывает открытые задачи прошлых недель
CREATE INDEX IF NOT EXISTS protocol_tasks_status_idx ON protocol_tasks (status);
-- «не сдвинулось» ищется по якорю метрики
CREATE INDEX IF NOT EXISTS protocol_tasks_metric_idx ON protocol_tasks (metric_key, unit_id);


-- ── RLS ───────────────────────────────────────────────────────────────────────
-- Политики нужны И для anon, И для authenticated: иначе залогиненные в дашборд
-- получают 403 там, где анонимные читают (проверено на грабли 2026-08-31).
-- Запись — только для authenticated: отчёт формируется и задачи вносятся из-под
-- логина.

ALTER TABLE report_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON report_snapshots FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON report_snapshots FOR SELECT TO authenticated USING (true);
CREATE POLICY authenticated_insert ON report_snapshots FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY authenticated_update ON report_snapshots FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
GRANT SELECT ON TABLE report_snapshots TO anon;
GRANT SELECT, INSERT, UPDATE ON TABLE report_snapshots TO authenticated;

ALTER TABLE protocol_tasks ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON protocol_tasks FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON protocol_tasks FOR SELECT TO authenticated USING (true);
CREATE POLICY authenticated_insert ON protocol_tasks FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY authenticated_update ON protocol_tasks FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
GRANT SELECT ON TABLE protocol_tasks TO anon;
GRANT SELECT, INSERT, UPDATE ON TABLE protocol_tasks TO authenticated;
