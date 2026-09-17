# Rescales our own weekday-shape baseline (revenue_forecast_daily.forecast_revenue)
# so that each (unit, month) with a leadership plan sums exactly to that plan's
# figure — the within-month distribution (which weekdays are busier, etc.) still
# comes from our own median-based forecast, only the monthly total is leadership's.
#
# Always rescales FROM the untouched forecast_revenue baseline (never from its own
# previous output), so re-running this after compute_revenue_forecast.py refreshes
# the baseline is safe/idempotent — it won't compound drift across runs.
import calendar
from datetime import date

from db import get_connection, get_cursor


def main():
    conn = get_connection()
    cur = get_cursor(conn)

    cur.execute("SELECT unit_id, month, plan_revenue FROM leadership_revenue_plan")
    plans = cur.fetchall()

    updates = []
    for p in plans:
        unit_id = p["unit_id"]
        month = p["month"] if isinstance(p["month"], date) else date.fromisoformat(p["month"])
        plan_revenue = float(p["plan_revenue"])

        last_day = calendar.monthrange(month.year, month.month)[1]
        month_start = month.isoformat()
        month_end = date(month.year, month.month, last_day).isoformat()

        cur.execute("""
            SELECT date, forecast_revenue FROM revenue_forecast_daily
            WHERE unit_id = %s AND date BETWEEN %s AND %s
        """, (unit_id, month_start, month_end))
        days = cur.fetchall()
        if not days:
            print(f"  skip {unit_id} {month_start}: no baseline forecast days found")
            continue

        baseline_sum = sum(float(d["forecast_revenue"]) for d in days)
        if not baseline_sum:
            print(f"  skip {unit_id} {month_start}: baseline sum is 0")
            continue

        scale = plan_revenue / baseline_sum
        for d in days:
            d_date = d["date"] if isinstance(d["date"], date) else date.fromisoformat(d["date"])
            rescaled = round(float(d["forecast_revenue"]) * scale, 2)
            updates.append((d_date, unit_id, rescaled))

    if updates:
        cur.executemany("""
            UPDATE revenue_forecast_daily
            SET leadership_forecast_revenue = %s
            WHERE date = %s AND unit_id = %s
        """, [(rescaled, d_date, unit_id) for d_date, unit_id, rescaled in updates])
        conn.commit()

    conn.close()
    print(f"Updated {len(updates)} days across {len(plans)} unit-months.")


if __name__ == "__main__":
    main()
