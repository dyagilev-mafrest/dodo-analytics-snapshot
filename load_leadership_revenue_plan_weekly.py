# One-off manual import of leadership's WEEKLY revenue plan, network-wide
# (Якутск целиком, не по юнитам) — source: screenshot shared by the user,
# "Пиццерия-город-регион-страна-неделя" Google Sheet. Re-run with updated
# numbers whenever leadership shares a revised/extended weekly plan.
from datetime import date

from db import get_connection, get_cursor

# (week_start "DD.MM.YYYY", их факт-выручка, план), snapshot taken 2026-07-30.
ROWS = [
    ("05.01.2026", 37_126_034, 37_124_661),
    ("12.01.2026", 31_216_392, 37_200_660),
    ("19.01.2026", 30_391_761, 37_357_016),
    ("26.01.2026", 29_406_488, 32_084_250),
    ("02.02.2026", 31_116_315, 34_553_250),
    ("09.02.2026", 33_335_333, 34_633_250),
    ("16.02.2026", 33_825_162, 34_553_490),
    ("23.02.2026", 35_589_266, 34_438_709),
    ("02.03.2026", 38_693_028, 37_403_619),
    ("09.03.2026", 32_088_286, 34_003_455),
    ("16.03.2026", 31_794_838, 32_303_282),
    ("23.03.2026", 33_280_253, 34_010_455),
    ("30.03.2026", 35_924_756, 33_691_393),
    ("06.04.2026", 30_703_756, 33_566_468),
    ("13.04.2026", 31_099_748, 33_050_030),
    ("20.04.2026", 31_731_073, 33_966_468),
    ("27.04.2026", 33_652_917, 34_373_294),
    ("04.05.2026", 31_485_518, 38_993_968),
    ("11.05.2026", 31_586_994, 35_449_062),
    ("18.05.2026", 33_495_409, 33_676_608),
    ("25.05.2026", 37_582_979, 33_700_379),
    ("01.06.2026", 36_126_287, 36_236_091),
    ("08.06.2026", 33_531_613, 36_259_091),
    ("15.06.2026", 33_935_084, 36_203_033),
    ("22.06.2026", 34_068_471, 36_598_541),
    ("29.06.2026", 40_975_894, 33_772_854),
    ("06.07.2026", 37_733_741, 33_724_193),
    ("13.07.2026", 32_712_717, 32_741_935),
    ("20.07.2026", 33_254_525, 34_042_279),
]


def main():
    conn = get_connection()
    cur = get_cursor(conn)

    rows = [
        (
            date(*reversed([int(p) for p in week_start.split(".")])),
            plan,
            actual,
        )
        for week_start, actual, plan in ROWS
    ]

    import psycopg2.extras
    psycopg2.extras.execute_values(cur, """
        INSERT INTO leadership_revenue_plan_weekly (week_start, plan_revenue, leadership_actual_revenue)
        VALUES %s
        ON CONFLICT (week_start) DO UPDATE SET
            plan_revenue = EXCLUDED.plan_revenue,
            leadership_actual_revenue = EXCLUDED.leadership_actual_revenue
    """, rows, page_size=200)
    conn.commit()
    conn.close()
    print(f"Upserted {len(rows)} rows.")


if __name__ == "__main__":
    main()
