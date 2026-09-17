# Упущенная выручка от стопов ПРОДУКТА, у которых нет ингредиентной причины.
#
# Метрика «Стопы продуктов» у Dodo IS складывается из двух явлений:
#   1) стоп ингредиента, развёрнутый по рецептуре на затронутые продукты
#      -> compute_ingredient_stop_impact.py -> ingredient_stop_impact
#   2) продукт выключили сам по себе, ингредиента за ним нет
#      -> этот скрипт -> product_stop_impact
# В выгрузке «Детали каждого стопа» второе видно по пустой колонке «Наименование
# ингредиента»: за 01.07-31.08.2026 это 595 строк и 875 699 ₽ — 62% всего, что есть
# у них и нет у нас (см. issue #15).
#
# Правило отбора: стоп продукта идёт в метрику, если НИ НА ОДИН его день нет
# ингредиентного вклада по той же паре (пиццерия, продукт). Проверено против эталона:
# ловит 480 из 592 их ключей (81% по числу, 66% по деньгам), а ложные срабатывания
# стоят 4 983 ₽ на 2 030 ключей — практически бесплатны.
#
# ⚠️ Это же правило в первой попытке (14.09.2026, до загрузки полной карты рецептур)
# давало 70% эталона на одной неделе и 148% на другой. Причина: «нет ингредиентного
# вклада» означало тогда не «нет ингредиентной причины», а «ингредиента нет в карте».
# Правило заработало только после того, как карта закрыла 1435 стопов из 1435.
# Если сходимость снова поедет — проверять сначала покрытие карты, а не пороги здесь.
#
# Деньги считаются той же формулой, что и у стопов продуктов вообще: переиспользуем
# helpers из compute_lost_revenue.py, включая ПОДНЕВНУЮ разбивку (compute_days_for_stop).
# Подневно, а не одним числом на стоп, потому что стоп живёт неделями и привязка всей
# суммы к дате начала ломает форму по периодам (та же причина, что в миграции 059).
import argparse
from datetime import date, datetime, timedelta

import psycopg2.extras

from db import get_connection, get_cursor
from compute_lost_revenue import (
    MAX_LOOKBACK_WEEKS,
    compute_days_for_stop,
    load_long_stop_days,
    load_revenue_map,
    load_schedules,
)

ASSORTMENT = ("Новинка", "Обязательный ассортимент")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="фильтр по дате начала стопа")
    parser.add_argument("--to-date", required=True)
    args = parser.parse_args()

    from_date = date.fromisoformat(args.from_date)
    to_date = date.fromisoformat(args.to_date)
    schedules = load_schedules()

    conn = get_connection()
    cur = get_cursor(conn)

    # Дни, где по паре (пиццерия, продукт) уже есть ингредиентная причина.
    cur.execute("""
        SELECT s.unit_id, i.product_uuid, i.stop_date
        FROM ingredient_stop_impact i JOIN stop_events s ON s.id = i.stop_event_id
        WHERE i.stop_date BETWEEN %s AND %s
    """, (from_date - timedelta(days=MAX_LOOKBACK_WEEKS * 7), to_date))
    covered = {(r["unit_id"], r["product_uuid"], r["stop_date"]) for r in cur.fetchall()}
    print(f"Дней с ингредиентной причиной: {len(covered)}")

    cur.execute("""
        SELECT s.id, s.unit_id, s.entity_id, s.started_at_local, s.ended_at_local, s.reason,
               p.classification
        FROM stop_events s
        JOIN product_classification p ON p.product_uuid = s.entity_id
        WHERE s.event_type = 'product' AND s.entity_id IS NOT NULL
          AND p.classification = ANY(%s)
          AND s.started_at_local::date BETWEEN %s AND %s
    """, (list(ASSORTMENT), args.from_date, args.to_date))
    stops = cur.fetchall()
    print(f"Стопов продуктов в ассортименте: {len(stops)}")

    # Открытый стоп тянется до конца диапазона — так же, как в load_long_stop_days.
    open_end = datetime.combine(to_date + timedelta(days=1), datetime.min.time())

    def days_of(row):
        end = row["ended_at_local"] or open_end
        d = row["started_at_local"].date()
        while d <= min(end.date(), to_date):
            yield d
            d += timedelta(days=1)

    selected = [s for s in stops
                if not any((s["unit_id"], s["entity_id"], d) in covered for d in days_of(s))]
    print(f"  из них без ингредиентной причины: {len(selected)}")
    if not selected:
        conn.close()
        return

    unit_ids = {s["unit_id"] for s in selected}
    product_ids = {s["entity_id"] for s in selected}
    lookback_start = from_date - timedelta(weeks=MAX_LOOKBACK_WEEKS)
    print("Загружаю базу для формулы...")
    long_stop_days = load_long_stop_days(cur, unit_ids, product_ids, lookback_start, to_date)
    revenue_map = load_revenue_map(cur, unit_ids, product_ids, lookback_start, to_date)

    rows = []
    for s in selected:
        end = s["ended_at_local"] or open_end
        for day, rub in compute_days_for_stop(schedules, long_stop_days, revenue_map,
                                              s["unit_id"], s["entity_id"],
                                              s["started_at_local"], end):
            if from_date <= day <= to_date and rub:
                rows.append((s["id"], s["unit_id"], s["entity_id"], day, rub,
                             s["reason"], s["classification"]))
    print(f"Строк к записи: {len(rows)}")

    # Пересчитываем диапазон с нуля: правило отбора может измениться после дозагрузки
    # карты рецептур, и старые строки стали бы «призраками».
    cur.execute("DELETE FROM product_stop_impact WHERE stop_date BETWEEN %s AND %s",
                (from_date, to_date))
    psycopg2.extras.execute_values(cur, """
        INSERT INTO product_stop_impact
            (stop_event_id, unit_id, product_uuid, stop_date, lost_revenue, reason, product_classification)
        VALUES %s
        ON CONFLICT (stop_event_id, stop_date) DO UPDATE SET
            lost_revenue = EXCLUDED.lost_revenue,
            reason = EXCLUDED.reason,
            product_classification = EXCLUDED.product_classification
    """, rows, page_size=500)
    conn.commit()
    cur.execute("SELECT COUNT(*) n, ROUND(SUM(lost_revenue)::numeric, 0) rub FROM product_stop_impact")
    r = cur.fetchone()
    print(f"В таблице: {r['n']} строк, {r['rub']} ₽")
    conn.close()


if __name__ == "__main__":
    main()
