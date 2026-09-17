import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

DB_PATH = "dodo.db"
USE_CLOUD_DB = os.getenv("USE_CLOUD_DB", "false").lower() == "true"

if USE_CLOUD_DB:
    import psycopg2
    import psycopg2.extras


def get_connection():
    """Открываем подключение либо к облачному PostgreSQL, либо к локальному SQLite."""
    if USE_CLOUD_DB:
        conn = psycopg2.connect(
            host=os.getenv("SUPABASE_HOST"),
            port=os.getenv("SUPABASE_PORT"),
            dbname=os.getenv("SUPABASE_DATABASE"),
            user=os.getenv("SUPABASE_USER"),
            password=os.getenv("SUPABASE_PASSWORD"),
            client_encoding="UTF8",
            options="-c statement_timeout=0",
        )
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn


def get_cursor(conn):
    """Возвращает курсор. Для PostgreSQL — с доступом к полям по имени, как у sqlite3.Row."""
    if USE_CLOUD_DB:
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        return conn.cursor()


def placeholder():
    """SQLite использует ?, PostgreSQL использует %s."""
    return "%s" if USE_CLOUD_DB else "?"


def init_db():
    """Создаём таблицы, если их ещё нет."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_sales (
            date TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            sales REAL NOT NULL,
            orders_count INTEGER NOT NULL,
            loaded_at TEXT NOT NULL,
            PRIMARY KEY (date, unit_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sales_breakdown (
            date TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            order_source TEXT NOT NULL,
            sales_channel TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            sales REAL NOT NULL,
            orders_count INTEGER NOT NULL,
            PRIMARY KEY (date, unit_id, order_source, sales_channel, payment_method)
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Таблицы готовы (созданы или уже существовали).")


def save_daily_sales(rows):
    """
    Сохраняем дневные продажи в БД.
    `rows` — список словарей из ответа API (поле result).
    Используем INSERT OR REPLACE (SQLite) / ON CONFLICT DO UPDATE (PostgreSQL) —
    повторный запуск перезапишет данные за тот же день.
    """
    if not rows:
        print("Нечего сохранять — пустой список.")
        return

    conn = get_connection()
    cursor = get_cursor(conn)
    loaded_at = datetime.now().isoformat(timespec="seconds")
    p = placeholder()

    sales_count = 0
    breakdown_count = 0

    for row in rows:
        if USE_CLOUD_DB:
            cursor.execute(f"""
                INSERT INTO daily_sales
                    (date, unit_id, sales, orders_count, loaded_at)
                VALUES ({p}, {p}, {p}, {p}, {p})
                ON CONFLICT (date, unit_id) DO UPDATE SET
                    sales = EXCLUDED.sales,
                    orders_count = EXCLUDED.orders_count,
                    loaded_at = EXCLUDED.loaded_at
            """, (
                row["date"], row["unitId"], row["sales"],
                row["ordersCount"], loaded_at,
            ))
        else:
            cursor.execute(f"""
                INSERT OR REPLACE INTO daily_sales
                    (date, unit_id, sales, orders_count, loaded_at)
                VALUES ({p}, {p}, {p}, {p}, {p})
            """, (
                row["date"], row["unitId"], row["sales"],
                row["ordersCount"], loaded_at,
            ))
        sales_count += 1

        cursor.execute(f"""
            DELETE FROM sales_breakdown WHERE date = {p} AND unit_id = {p}
        """, (row["date"], row["unitId"]))

        for item in row.get("salesBreakdown", []):
            if USE_CLOUD_DB:
                cursor.execute(f"""
                    INSERT INTO sales_breakdown
                        (date, unit_id, order_source, sales_channel,
                         payment_method, sales, orders_count)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                    ON CONFLICT (date, unit_id, order_source, sales_channel, payment_method)
                    DO UPDATE SET
                        sales = EXCLUDED.sales,
                        orders_count = EXCLUDED.orders_count
                """, (
                    row["date"], row["unitId"], item["orderSource"],
                    item["salesChannel"], item["paymentMethod"],
                    item["sales"], item["ordersCount"],
                ))
            else:
                cursor.execute(f"""
                    INSERT OR REPLACE INTO sales_breakdown
                        (date, unit_id, order_source, sales_channel,
                         payment_method, sales, orders_count)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                """, (
                    row["date"], row["unitId"], item["orderSource"],
                    item["salesChannel"], item["paymentMethod"],
                    item["sales"], item["ordersCount"],
                ))
            breakdown_count += 1

    conn.commit()
    conn.close()
    print(f"💾 Сохранено: {sales_count} дневных записей, {breakdown_count} строк разбивки.")


def show_daily_sales():
    """Покажем, что лежит в таблице — для проверки."""
    conn = get_connection()
    cursor = get_cursor(conn)
    cursor.execute("""
        SELECT date, unit_id, sales, orders_count, loaded_at
        FROM daily_sales
        ORDER BY date DESC, unit_id
    """)
    rows = cursor.fetchall()
    conn.close()

    print(f"\n=== СОДЕРЖИМОЕ daily_sales ({len(rows)} строк) ===")
    for r in rows:
        print(f"  {r['date']} | {r['unit_id'][:8]}... | {r['sales']:>12,.2f} ₽ | {r['orders_count']:>4} чеков | loaded {r['loaded_at']}")