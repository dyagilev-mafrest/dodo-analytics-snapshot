-- Эталон Dodo IS по «Рейтингу клиентов» (РК) — ручная выгрузка виджета
-- `Client Rating Overall Table` из дашборда клиентского опыта.
--
-- Зачем: наш расчёт из публичного API `customer-feedback/customer-ratings` даёт
-- систематически чуть более низкую оценку и на 2–15% больше самих оценок, чем
-- дашборд Dodo IS (замер 02.09.2026 за неделю 24–30.08: по сети доставка 4.7833
-- против 4.7991). Причина — разное определение счётчика оценок, из публичного API
-- она не лечится, см. docs/customer-rating-methodology.md. Поэтому официальную
-- цифру держим рядом со своей, а не пытаемся подогнать расчёт.
--
-- Гранулярность периода произвольная (period_start..period_end включительно):
-- выгрузка помечает строку датой начала периода.
--
-- exported_at — дата выгрузки. У рейтингов дозревание не измеряли (в отличие от
-- стопов продуктов), но правило то же: без даты выгрузки нельзя понять, что с чем
-- сравнивали.
CREATE TABLE IF NOT EXISTS dodois_client_rating (
    period_start             date NOT NULL,
    period_end               date NOT NULL,  -- включительно
    unit_name                text NOT NULL,
    -- РК: средние оценки заказа, шкала 1–5
    rating_total             numeric,
    rating_delivery          numeric,
    rating_restaurant        numeric,
    -- сколько оценок стоит за каждой средней
    ratings_count            integer,
    ratings_count_delivery   integer,
    ratings_count_restaurant integer,
    -- знаменатели из той же выгрузки: заказы, к которым оценки привязаны.
    -- Именно они выдают, что счётчик Dodo привязан к ЗАКАЗАМ, а не к событиям
    -- оценки: «Доля оценок от заказов на доставку через приложение» в выгрузке
    -- равна ratings_count_delivery / delivery_orders_app.
    delivery_orders_app      integer,
    delivery_orders_all      integer,
    restaurant_orders_app    integer,
    restaurant_orders_all    integer,
    exported_at              date NOT NULL,
    loaded_at                timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (period_start, period_end, unit_name)
);

ALTER TABLE dodois_client_rating ENABLE ROW LEVEL SECURITY;
CREATE POLICY anon_read ON dodois_client_rating FOR SELECT TO anon USING (true);
CREATE POLICY authenticated_read ON dodois_client_rating FOR SELECT TO authenticated USING (true);
GRANT SELECT ON TABLE dodois_client_rating TO anon;
GRANT SELECT ON TABLE dodois_client_rating TO authenticated;
