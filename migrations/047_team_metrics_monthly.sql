-- Метрики команды (кухня/линейный персонал и курьеры) — ручной ежемесячный
-- HR-отчёт, Google-таблица "Метрики команды" (владелец — HR/операционный
-- отдел), листы "Метрики команды" (кухня) и "Метрики команды куры" (курьеры).
-- Powers блок "Команда" в dodo-analytics-dashboard, раздел "Месяц".
--
-- Источник — вертикально повторяющийся блок на каждый месяц: 7 строк по
-- пиццериям + 1 строка "общ по сети" (переименована в unit_name='Сеть').
-- get_team_metrics.py читает блоки по метке в первой колонке ("Месяц [Год]
-- Якутск-1"), не по фиксированному номеру строки.
--
-- "Укомплект, %" — простая колонка без уточнения (НЕ "укомплект по плану
-- набора", это отдельная соседняя колонка, в БД не грузим — решение
-- пользователя 2026-08-25).
CREATE TABLE IF NOT EXISTS team_metrics_kitchen_monthly (
    month                  date NOT NULL,
    unit_name              text NOT NULL,   -- "Якутск-1".."Якутск-7" или "Сеть"
    completion_pct         numeric,         -- "Укомплект, %"
    turnover_pct           numeric,         -- "Текучесть"
    experienced_share_pct  numeric,         -- "Доля опытных в активном штате"
    trainee_share_pct      numeric,         -- вычислено: активный_штат_стажеры / (опытные+стажеры+новички) — своей % колонки в источнике нет
    hired_count            integer,         -- "Принято"
    terminated_count       integer,         -- "Уволено" (знак как в источнике — отрицательное число)
    loaded_at              timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, unit_name)
);

CREATE TABLE IF NOT EXISTS team_metrics_courier_monthly (
    month                  date NOT NULL,
    unit_name              text NOT NULL,
    completion_pct         numeric,         -- "Укомплект, %"
    turnover_pct           numeric,         -- "Текучесть"
    hired_count            integer,         -- "Принято"
    terminated_count       integer,         -- "Уволено" (отрицательное число)
    loaded_at              timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, unit_name)
);

ALTER TABLE team_metrics_kitchen_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON team_metrics_kitchen_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON team_metrics_kitchen_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE team_metrics_kitchen_monthly TO anon;
GRANT SELECT ON TABLE team_metrics_kitchen_monthly TO authenticated;

ALTER TABLE team_metrics_courier_monthly ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON team_metrics_courier_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON team_metrics_courier_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE team_metrics_courier_monthly TO anon;
GRANT SELECT ON TABLE team_metrics_courier_monthly TO authenticated;
