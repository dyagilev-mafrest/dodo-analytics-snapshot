-- Сценарный прогноз выручки по сети, помесячно — из артефакта «Прогноз выручки
-- Якутск Dodo Pizza 2026-2027» (составлен 25.06.2026, открытия актуализированы
-- 09.09.2026). Питает линию прогноза на графике выручки месячного отчёта.
--
-- НЕ ПУТАТЬ С ТРЕМЯ СОСЕДЯМИ:
--   leadership_revenue_plan_network — обещание совета, утверждено в 1 квартале;
--   revenue_forecast_daily          — наш ретроспективный прогноз по медиане
--                                     непраздничных недель (compute_revenue_forecast.py);
--   эта таблица                     — сценарная модель аналитика на год вперёд.
-- Три разные вещи: план, статистическая экстраполяция и сценарий. Сводить их в
-- одну колонку нельзя, поэтому и таблицы три.
--
-- Хранится модельный ряд, а не «авторитетный» из того же артефакта: там
-- январь-август подменены фактом, и линия, с которой факт сравнивают, содержала
-- бы в себе этот же факт — отклонение по закрытым месяцам всегда выходило бы
-- нулевым.
--
-- Сценарий колонкой: в артефакте их пять (кризисный, пессимистичный, базовый,
-- оптимистичный, рост). Сейчас грузим базовый; остальные лягут строками, если
-- совет захочет вилку.
CREATE TABLE IF NOT EXISTS revenue_forecast_scenario_monthly (
    month        date NOT NULL,   -- первое число месяца
    scenario     text NOT NULL,   -- 'base', далее при надобности 'crisis', 'growth', ...
    revenue_rub  numeric NOT NULL,
    source_note  text,            -- откуда ряд и на какую дату
    loaded_at    timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (month, scenario)
);

ALTER TABLE revenue_forecast_scenario_monthly ENABLE ROW LEVEL SECURITY;

-- Обе роли обязательны: anon читает публично, authenticated — залогиненные в
-- дашборд. Без второй политики они получают 403 (грабли, описанные в agents.md).
CREATE POLICY anon_read ON revenue_forecast_scenario_monthly FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON revenue_forecast_scenario_monthly FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE revenue_forecast_scenario_monthly TO anon;
GRANT SELECT ON TABLE revenue_forecast_scenario_monthly TO authenticated;
