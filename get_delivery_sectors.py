# Dodo IS -> PostgreSQL (Supabase): delivery_sectors (sector reference/definition data)
#
# Endpoint: delivery/delivery-sectors — found in the official OpenAPI specs
# (docs/dodo-is-openapi/dodo-is-api.yaml), never used before 2026-08-28. Gives a real
# sectorId per sector (stop_events and hourly_revenue_by_sector both key sectors by
# name only), plus isDeleted/isStopped/isSubSector/geometry.
#
# Snapshot, not a history: run standalone whenever the sector map needs refreshing.
# Up to 30 units per request — all 7 Yakutsk units fit in one call.
import json

from get_stops import api_get, make_headers, UNITS_FILE
from db import get_connection, get_cursor


def fetch_delivery_sectors(unit_ids: list[str], headers: dict) -> list[dict]:
    data = api_get(
        "delivery/delivery-sectors",
        {"units": ",".join(unit_ids), "showDeleted": "true", "showSubSectors": "true"},
        headers,
    )
    return data.get("deliverySectors", [])


def upsert_rows(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras

    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO delivery_sectors
            (unit_id, sector_id, unit_name, sector_name, is_deleted, is_stopped, is_subsector, geometry, updated_at)
        VALUES %s
        ON CONFLICT (unit_id, sector_id) DO UPDATE SET
            unit_name    = EXCLUDED.unit_name,
            sector_name  = EXCLUDED.sector_name,
            is_deleted   = EXCLUDED.is_deleted,
            is_stopped   = EXCLUDED.is_stopped,
            is_subsector = EXCLUDED.is_subsector,
            geometry     = EXCLUDED.geometry,
            updated_at   = now()
        """,
        [
            (
                row["unitId"].lower(), row["sectorId"].lower(), row.get("unitName"), row.get("sectorName"),
                bool(row.get("isDeleted")), bool(row.get("isStopped")), bool(row.get("isSubSector")),
                json.dumps(row.get("geometry")) if row.get("geometry") else None,
            )
            for row in rows
        ],
        page_size=200,
        template="(%s, %s, %s, %s, %s, %s, %s, %s, now())",
    )
    conn.commit()
    conn.close()


def main():
    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)

    headers = make_headers()
    rows = fetch_delivery_sectors([u["id"] for u in units], headers)
    print(f"Получено {len(rows)} секторов по {len(units)} пиццериям")
    upsert_rows(rows)
    print("Done.")


if __name__ == "__main__":
    main()
