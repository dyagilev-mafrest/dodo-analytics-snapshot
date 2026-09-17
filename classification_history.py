# SCD-2 для справочников классификации (product_classification / material_classification).
#
# Справочник Dodo IS пересобирается по скользящему окну «расход за последние 21 день»
# (см. миграцию 052 и docs/stops-lost-revenue-reconciliation.md), поэтому статус продукта
# на прошлой неделе и сегодня — разные вещи. Плоские таблицы хранят последний снимок;
# здесь копится история, чтобы расчёт мог спросить статус НА ДАТУ СТОПА.
#
# Вызывается из load_product_classification.py / load_material_classification.py сразу
# после обычного upsert'а. Идемпотентно: повторная загрузка того же снимка ничего
# не меняет, загрузка снимка со старой датой игнорируется (историю назад не двигаем).
from datetime import date

import psycopg2.extras

from db import get_connection, get_cursor


def assert_snapshot_not_older(table: str, snapshot_date: date) -> None:
    """Падает, если снимок старее уже загруженного.

    Вызывать ДО записи в плоскую таблицу: у неё upsert по ключу, и устаревшая
    выгрузка молча затрёт свежие строки (ровно так и случилось 01.09.2026 —
    вкладка `ingredients` гугл-таблицы отставала на 19 месяцев и перезаписала
    234 строки, потому что проверка стояла только внутри append_snapshot,
    то есть уже после upsert'а).
    """
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"SELECT MAX(valid_from) AS m FROM {table}_history")
    latest = cur.fetchone()["m"]
    conn.close()
    if latest and snapshot_date < latest:
        raise SystemExit(
            f"Загружаемый снимок {table} датирован {snapshot_date}, а в истории уже есть "
            f"{latest}. Источник устарел — загрузка отменена, ничего не изменено."
        )


def append_snapshot(table: str, key_column: str, rows: list[dict], fields: list[str],
                    snapshot_date: date) -> dict[str, int]:
    """Добавляет снимок в `{table}_history` по правилам SCD-2.

    rows       — строки снимка, ключ `key_column` + поля `fields`
    fields     — какие поля версионируем; изменение ЛЮБОГО из них открывает новую версию
    Возвращает {'new': .., 'changed': .., 'closed': .., 'same': ..}
    """
    hist = f"{table}_history"
    conn = get_connection()
    cur = get_cursor(conn)

    # Снимок старше уже загруженного не должен переписывать историю задним числом.
    cur.execute(f"SELECT MAX(valid_from) AS m FROM {hist}")
    latest = cur.fetchone()["m"]
    if latest and snapshot_date < latest:
        conn.close()
        raise SystemExit(
            f"В {hist} уже есть снимок за {latest}, а загружаемый — за {snapshot_date}. "
            "Загрузка более старого снимка не поддерживается."
        )

    cur.execute(
        f"SELECT {key_column}, {', '.join(fields)} FROM {hist} WHERE valid_to IS NULL"
    )
    open_rows = {r[key_column]: tuple(r[f] for f in fields) for r in cur.fetchall()}

    incoming = {r[key_column]: tuple(r.get(f) for f in fields) for r in rows}

    to_close: list[str] = []      # ключи, чью открытую версию надо закрыть
    to_insert: list[tuple] = []   # новые версии

    for key, values in incoming.items():
        if key not in open_rows:
            to_insert.append((key, snapshot_date, None) + values)
        elif open_rows[key] != values:
            to_close.append(key)
            to_insert.append((key, snapshot_date, None) + values)

    # Позиция выпала из выгрузки целиком — закрываем версию, но новую не открываем:
    # чем она стала, мы не знаем, и придумывать статус нельзя.
    dropped = [k for k in open_rows if k not in incoming]
    to_close.extend(dropped)

    if to_close:
        cur.execute(
            f"UPDATE {hist} SET valid_to = %s "
            f"WHERE valid_to IS NULL AND {key_column} = ANY(%s)",
            (snapshot_date, to_close),
        )
    if to_insert:
        cols = f"{key_column}, valid_from, valid_to, " + ", ".join(fields)
        placeholders = "(" + ", ".join(["%s"] * (3 + len(fields))) + ")"
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO {hist} ({cols}) VALUES %s "
            f"ON CONFLICT ({key_column}, valid_from) DO NOTHING",
            to_insert,
            template=placeholders,
            page_size=500,
        )
    conn.commit()
    conn.close()

    changed = len(to_close) - len(dropped)
    return {
        "new": len(to_insert) - changed,
        "changed": changed,
        "closed": len(dropped),
        "same": len(incoming) - len(to_insert),
    }


def print_summary(name: str, stats: dict[str, int], snapshot_date: date) -> None:
    print(
        f"История {name} на {snapshot_date}: новых {stats['new']}, "
        f"сменили статус {stats['changed']}, выпали из выгрузки {stats['closed']}, "
        f"без изменений {stats['same']}"
    )

STALE_AFTER_DAYS = 14  # окно справочника — 21 день, так что двухнедельный застой уже опасен


def warn_if_stale(table: str, snapshot_date: date, max_age_days: int = STALE_AFTER_DAYS) -> bool:
    """Печатает предупреждение, если источник снимков, похоже, замер. Возвращает True, если замер.

    Зачем: справочник Dodo IS пересобирается по скользящему окну «расход за 21 день»,
    а гугл-таблица DP_sync наполняется человеком. Если её перестанут обновлять, наша
    история просто перестанет пополняться — и выглядеть это будет ровно так же, как
    «за неделю ничего не поменялось». Молчаливый застой хуже ошибки: расчёт продолжит
    выдавать числа, просто по всё более устаревшим статусам.

    Два независимых признака:
      1. сам снимок датирован давно (для материалов дата берётся из таблицы);
      2. история давно не менялась — ни одной новой версии за max_age_days.
    """
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute(f"SELECT MAX(valid_from) AS m FROM {table}_history")
    last_change = cur.fetchone()["m"]
    conn.close()

    today = date.today()
    problems = []
    snapshot_age = (today - snapshot_date).days
    if snapshot_age > max_age_days:
        problems.append(f"снимок датирован {snapshot_date} — это {snapshot_age} дн. назад")
    if last_change:
        change_age = (today - last_change).days
        if change_age > max_age_days:
            problems.append(f"история не менялась с {last_change} — {change_age} дн.")

    if problems:
        print()
        print("!" * 72)
        print(f"! ВНИМАНИЕ: источник справочника {table}, похоже, не обновляется.")
        for p in problems:
            print(f"!   - {p}")
        print("! Статусы продуктов и материалов живут в окне 21 день. Пока источник стоит,")
        print("! расчёт «Стопов ключевых ингредиентов» опирается на всё более старые статусы.")
        print("! Обновите гугл-таблицу «DP_sync Product and Ingredient classification»")
        print("! (инструкция на её вкладке «Как обновить») и прогоните загрузчик снова.")
        print("!" * 72)
    return bool(problems)

