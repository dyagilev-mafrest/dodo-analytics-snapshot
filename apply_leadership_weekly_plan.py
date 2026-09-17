# Rescales our own weekday-shape baseline so each week with a leadership WEEKLY
# plan (network-wide, not per-unit) sums exactly to that plan — more precise than
# the monthly version (apply_leadership_revenue_plan.py) where both exist. Run
# this AFTER the monthly script: it overwrites leadership_forecast_revenue for
# the days covered by leadership_revenue_plan_weekly, leaving the monthly-derived
# values in place for weeks outside that table's range (e.g. future weeks).
#
# Same scale factor is applied to every unit within a week (network total is all
# we have, no per-unit weekly split), preserving each unit's own weekday shape.
from datetime import date, timedelta

from db import get_connection, get_cursor


def main():
    conn = get_connection()
    cur = get_cursor(conn)

    cur.execute("SELECT week_start, plan_revenue FROM leadership_revenue_plan_weekly")
    weeks = cur.fetchall()

    updates = []
    for w in weeks:
        week_start = w["week_start"] if isinstance(w["week_start"], date) else date.fromisoformat(w["week_start"])
        week_end = week_start + timedelta(days=6)
        plan_revenue = float(w["plan_revenue"])

        cur.execute("""
            SELECT date, unit_id, forecast_revenue FROM revenue_forecast_daily
            WHERE date BETWEEN %s AND %s
        """, (week_start.isoformat(), week_end.isoformat()))
        days = cur.fetchall()
        if not days:
            print(f"  skip {week_start}: no baseline forecast days found")
            continue

        baseline_sum = sum(float(d["forecast_revenue"]) for d in days)
        if not baseline_sum:
            print(f"  skip {week_start}: baseline sum is 0")
            continue

        scale = plan_revenue / baseline_sum
        for d in days:
            d_date = d["date"] if isinstance(d["date"], date) else date.fromisoformat(d["date"])
            rescaled = round(float(d["forecast_revenue"]) * scale, 2)
            updates.append((d_date, d["unit_id"], rescaled))

    if updates:
        cur.executemany("""
            UPDATE revenue_forecast_daily
            SET leadership_forecast_revenue = %s
            WHERE date = %s AND unit_id = %s
        """, [(rescaled, d_date, unit_id) for d_date, unit_id, rescaled in updates])
        conn.commit()

    conn.close()
    print(f"Updated {len(updates)} days across {len(weeks)} weeks.")


if __name__ == "__main__":
    main()
