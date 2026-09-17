-- Индекс счастья сотрудников помесячно — лист «ИС весь» таблицы «HR метрики
-- Додо Якутск», та же таблица, что питает team_metrics_* (047).
--
-- Две величины в паре: сам индекс и заполняемость опроса. Вторая — не
-- украшение: индекс, посчитанный по трети коллектива, и индекс по девяноста
-- процентам это разные по надёжности цифры, и на совете первым делом спросят
-- «а сколько человек ответило». Поэтому заполняемость хранится рядом и
-- показывается в отчёте вместе с индексом.
--
-- Индекс бывает отрицательным (Якутск-1, август 2026: -5%) — это eNPS-подобная
-- величина, разность долей, а не доля. Никаких CHECK >= 0.
--
-- Строка «Среднее» листа ложится как unit_name='Сеть' — та же договорённость,
-- что в team_metrics_kitchen_monthly.
--
-- Норматива у показателя в реестре НЕТ: в статье-стандарте Dodo IS есть цифра
-- «индекс счастья >= 75%», но неизвестно, о какой из двух величин она — наши
-- значения индекса 26-44% против заполняемости 56-76%, и промахнуться здесь
-- значит объявить сети провал там, где его может не быть.
CREATE TABLE IF NOT EXISTS happiness_index_monthly (
    month             date NOT NULL,   -- первое число месяца
    unit_name         text NOT NULL,   -- «Якутск-1».. «ПРЦ» или «Сеть»
    index_pct         numeric,         -- ИС, в процентах; бывает отрицательным
    response_rate_pct numeric,         -- заполняемость опроса, в процентах
    loaded_at         timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, unit_name)
);

ALTER TABLE happiness_index_monthly ENABLE ROW LEVEL SECURITY;

-- Обе роли обязательны: anon читает публично, authenticated — залогиненные в
-- дашборд. Без второй политики они получают 403 (грабли, описанные в agents.md).
CREATE POLICY anon_read ON happiness_index_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON happiness_index_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE happiness_index_monthly TO anon;
GRANT SELECT ON TABLE happiness_index_monthly TO authenticated;
