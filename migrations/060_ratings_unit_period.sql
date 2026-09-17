-- Рейтинги Dodo IS по пиццериям: РКО (рейтинг клиентского опыта) и РС
-- (рейтинг стандартов) живым pull'ом из controlling-api вместо ручного xlsx.
--
-- Почему одна таблица на два рейтинга: оба эндпоинта
-- (/ratings/customer-experience и /ratings/standards) возвращают одинаковую
-- структуру UnitRating и различаются только периодом и ритмом проверок.
-- Разносить их по таблицам — это две копии одной формы и два места для правок.
--
-- Ритм у рейтингов разный и к календарному месяцу НЕ привязан:
--   РКО — проверка каждую неделю, публикация по понедельникам до 18:00;
--   РС  — две проверки в месяц, период иногда растягивается до трёх недель
--         из-за праздников (см. «Календарь периодов рейтинга стандартов»).
-- Поэтому ось периодов — period_from/period_to из самого ответа, а не наш
-- календарь: натягивание РС на месяц врёт всегда, а не иногда.
--
-- rate = NULL — это не «нет данных», а «в этом периоде проверки не было»:
-- у РС каждый период часть пиццерий остаётся без проверки (замер 11.09.2026:
-- 5 из 7). На экране показываем «—», в среднее по сети не берём.
--
-- avg_rate — среднее за окно ≈3 месяца: у РКО это 12 проверок, у РС 6. Оба
-- окна дают примерно квартал, поэтому в интерфейсе Dodo IS переключатель и
-- называется «За 3 месяца», а не числом проверок.
--
-- Официальные пороги по avg_rate (статьи-стандарты «Рейтинг клиентского опыта:
-- принципы и правила» и «Рейтинг стандартов: принципы и правила»):
--   < 85 — партнёр может потерять право на развитие;
--   < 80 — предписание об исправлении нарушений со сроком.
-- Локальная норма МАФРЕСТ (95) строже официальной — держим их парой, как с
-- временем доставки, чтобы 91 не читалась как провал по стандарту сети.
--
-- publish_status: 'Calculated' — предварительный результат, он ещё может
-- уехать после апелляций; подписывать это на экране обязательно, иначе на
-- встрече по предварительному баллу поставят задачу.
--
-- Эндпоинт отдаёт ТОЛЬКО текущий период, поэтому история копится прогонами.
-- Backfill прошлых периодов возможен через /ratings/{kind}/history — отдельно.
CREATE TABLE IF NOT EXISTS ratings_unit_period (
    kind            text NOT NULL,      -- 'customer-experience' | 'standards'
    unit_id         text NOT NULL,      -- lowercase: API отдаёт UPPERCASE, остальные таблицы хранят нижний регистр
    unit_name       text NOT NULL,
    period_from     date NOT NULL,
    period_to       date NOT NULL,      -- включительно
    rate            numeric,            -- балл за период, NULL = проверки не было
    avg_rate        numeric,            -- среднее за окно ≈3 месяца
    common_position integer,            -- место в общем рейтинге сети; по нему же сортируется таблица
    is_ranked       boolean,
    publish_status  text NOT NULL,
    published_at    timestamptz,
    loaded_at       timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, unit_id, period_from)
);

ALTER TABLE ratings_unit_period ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON ratings_unit_period FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON ratings_unit_period FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE ratings_unit_period TO anon;
GRANT SELECT ON TABLE ratings_unit_period TO authenticated;
