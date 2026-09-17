# Dodo IS -> PostgreSQL (Supabase): staff_headcount_daily
#
# SCOPE NOTE (2026-08-04): staff/positions/history's leavePositionOn/isActive
# do NOT reliably signal a real dismissal — verified live against the FULL
# history of all 408 staff across all 7 units: not a single staffId's
# chronologically-last record has leavePositionOn set. Every closed record
# in the data is a transfer/promotion continued by a later record; when
# someone actually leaves, their last record apparently just stays open
# forever with no closing signal in this endpoint. So turnover events
# (hire/dismissal counts, the by-category bar chart) are OUT OF SCOPE for
# now — this script only builds the headcount/experience-tier table, which
# doesn't depend on ever detecting a dismissal. Revisit turnover once
# staff/members/{id} is reachable (needs the staffmembers:read scope, not
# granted to this app — see project_dodo_team_section.md).
#
# Endpoints:
# - staff/positions/history — max 4 units per request (TooManyUnits on 5+),
#   batch accordingly. Returns FULL lifetime chains for any staffId that has
#   at least one record overlapping the requested from/to window (verified
#   live 2026-08-04) — so one wide-range call per batch is enough, no need to
#   walk day by day. Paginate with skip/take (isEndOfListReached), same
#   convention as get_delivery.py/get_restaurant.py. Used here only to know
#   who's actively holding a position on a given day (for headcount), not for
#   turnover.
# - staff/incentives-by-members — all 7 units in one request (no batching
#   needed, unlike positions/history). Gives per-shift clockInAtLocal/
#   clockOutAtLocal, used to compute cumulative worked hours per staffId.
#   No isEndOfListReached field observed — treated as non-paginated; fetched
#   in quarterly chunks to keep each response a manageable size (~15MB/quarter
#   for all 7 units at current volume).
#
# Methodology (see project memory project_dodo_team_section.md for the
# investigation history):
# - Experience tiers are by CUMULATIVE WORKED HOURS since the earliest
#   available API history (2016-01-01), not tenure duration or position name:
#     novice      <  25h
#     trainee     25–250h
#     experienced 250h+
# - Couriers aren't staffed as a position in staff/positions/history for our
#   7 units — they never appear in the active-roster headcount. They DO show
#   up in incentives-by-members (staffType: Courier) but that's harmless
#   here: headcount tiers are computed from positions/history's active
#   roster, which never includes them anyway.
import json
import time
from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
from dotenv import load_dotenv

from get_sales import get_access_token
from db import get_connection, get_cursor

load_dotenv()
BASE = "https://api.dodois.io/dodopizza/ru"
UNITS_FILE = "yakutsk_units.json"
HOURS_FLOOR = "2016-01-01"  # earliest date the API has any staff data for
NOVICE_MAX_HOURS = 25
TRAINEE_MAX_HOURS = 250


def make_headers():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}"}


def api_get(path: str, params: dict, headers: dict, retries: int = 6) -> dict:
    url = f"{BASE}/{path}"
    for attempt in range(retries):
        try:
            r = httpx.get(url, params=params, headers=headers, timeout=120)
            if r.status_code == 401:
                stale = headers.get("Authorization", "").removeprefix("Bearer ")
                headers["Authorization"] = f"Bearer {get_access_token(force_refresh=True, _stale_token=stale)}"
                continue
            if r.status_code == 429:
                if attempt == retries - 1:
                    r.raise_for_status()
                retry_after = float(r.headers.get("Retry-After", 0) or 0)
                wait = max(retry_after, 20 * (attempt + 1))
                print(f"  retry {attempt+1}/{retries}: 429, waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt+1}/{retries}: {e}")
            time.sleep(5 * (attempt + 1))
    return {}


def chunk(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def fetch_positions_history(unit_ids: list[str], frm: str, to: str, headers: dict) -> list[dict]:
    """Full lifetime chains for any staffId with a record overlapping [frm, to],
    batched by <=4 units per request (API limit) and paginated with skip/take."""
    all_rows = []
    for batch in chunk(unit_ids, 4):
        skip, take = 0, 1000
        while True:
            data = api_get("staff/positions/history", {
                "units": ",".join(batch),
                "from": f"{frm}T00:00:00",
                "to": f"{to}T00:00:00",
                "skip": skip, "take": take,
            }, headers)
            rows = data.get("history", [])
            all_rows.extend(rows)
            if data.get("isEndOfListReached", True):
                break
            skip += take
    return all_rows


def fetch_incentive_shifts(unit_ids: list[str], frm: str, to: str, headers: dict) -> list[dict]:
    """All shifts for all units in one call — no batching limit observed here,
    unlike positions/history. Caller chunks the date range for large pulls."""
    data = api_get("staff/incentives-by-members", {
        "units": ",".join(unit_ids),
        "from": f"{frm}T00:00:00",
        "to": f"{to}T00:00:00",
    }, headers)
    shifts = []
    for member in data.get("staffMembers", []):
        staff_id = member["staffId"]
        for s in member.get("shiftsDetailing", []):
            clock_in = s.get("clockInAtLocal")
            clock_out = s.get("clockOutAtLocal")
            if not clock_in or not clock_out:
                continue
            shifts.append((staff_id, clock_in, clock_out))
    return shifts


def quarter_ranges(start: str, end: str):
    """Yield (from, to) date strings covering [start, end) in ~3-month chunks."""
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d < end_d:
        nxt = min(date(d.year + (d.month + 3 - 1) // 12,
                        (d.month + 3 - 1) % 12 + 1, 1), end_d)
        yield str(d), str(nxt)
        d = nxt


# ---------------------------------------------------------------------------
# Cumulative hours per staff (for experience tiers)
# ---------------------------------------------------------------------------

def shift_hours(clock_in: str, clock_out: str) -> float:
    fmt = "%Y-%m-%dT%H:%M:%S"
    t_in = datetime.strptime(clock_in, fmt)
    t_out = datetime.strptime(clock_out, fmt)
    return max((t_out - t_in).total_seconds() / 3600.0, 0)


def build_hours_timeline(shifts: list[tuple]) -> dict[str, tuple[list, list]]:
    """staff_id -> (sorted clock-in dates, parallel cumulative-hours-after-that-shift)."""
    per_staff = defaultdict(list)
    for staff_id, clock_in, clock_out in shifts:
        per_staff[staff_id].append((clock_in[:10], shift_hours(clock_in, clock_out)))

    timelines = {}
    for staff_id, entries in per_staff.items():
        entries.sort(key=lambda e: e[0])
        dates, cum = [], []
        running = 0.0
        for d, hrs in entries:
            running += hrs
            dates.append(d)
            cum.append(running)
        timelines[staff_id] = (dates, cum)
    return timelines


def hours_as_of(timeline: dict, staff_id: str, day: str) -> float:
    entry = timeline.get(staff_id)
    if not entry:
        return 0.0
    dates, cum = entry
    idx = bisect_right(dates, day) - 1
    return cum[idx] if idx >= 0 else 0.0


def tier_of(hours: float) -> str:
    if hours < NOVICE_MAX_HOURS:
        return "novice"
    if hours < TRAINEE_MAX_HOURS:
        return "trainee"
    return "experienced"


# ---------------------------------------------------------------------------
# Headcount snapshot per day
# ---------------------------------------------------------------------------

def compute_headcount_daily(history_rows: list[dict], hours_timeline: dict,
                             start_date: str, end_date: str,
                             unit_names: dict[str, str]) -> list[dict]:
    """unit_names' keys double as the allowlist of units we report headcount
    for — history_rows can include transfer records into OTHER Dodo units
    outside our 7 (fetch_positions_history returns a staffId's full lifetime
    chain, not just the requested units), which must not leak into headcount."""
    counts = defaultdict(lambda: {"novice": 0, "trainee": 0, "experienced": 0})
    start_d, end_d = date.fromisoformat(start_date), date.fromisoformat(end_date)

    for r in history_rows:
        if r["unitId"] not in unit_names:
            continue
        take_on = date.fromisoformat(r["takePositionOn"])
        leave_on = date.fromisoformat(r["leavePositionOn"]) if r["leavePositionOn"] else None
        window_start = max(take_on, start_d)
        window_end = min(leave_on, end_d) if leave_on else end_d
        if window_start > window_end:
            continue
        d = window_start
        while d <= window_end:
            hrs = hours_as_of(hours_timeline, r["staffId"], str(d))
            counts[(str(d), r["unitId"])][tier_of(hrs)] += 1
            d += timedelta(days=1)

    rows = []
    for (day, unit_id), tiers in counts.items():
        rows.append({
            "date": day, "unit_id": unit_id, "unit_name": unit_names.get(unit_id),
            "novice_count": tiers["novice"], "trainee_count": tiers["trainee"],
            "experienced_count": tiers["experienced"],
            "active_count": tiers["novice"] + tiers["trainee"] + tiers["experienced"],
        })
    return rows


def upsert_headcount_daily(rows: list[dict]):
    if not rows:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO staff_headcount_daily
            (date, unit_id, unit_name, novice_count, trainee_count, experienced_count, active_count)
        VALUES %s
        ON CONFLICT (date, unit_id) DO UPDATE SET
            unit_name         = EXCLUDED.unit_name,
            novice_count      = EXCLUDED.novice_count,
            trainee_count     = EXCLUDED.trainee_count,
            experienced_count = EXCLUDED.experienced_count,
            active_count      = EXCLUDED.active_count
    """, [
        (r["date"], r["unit_id"], r["unit_name"], r["novice_count"],
         r["trainee_count"], r["experienced_count"], r["active_count"])
        for r in rows
    ], page_size=500)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# staff_hours_state: running cumulative-hours cache for incremental daily runs
# ---------------------------------------------------------------------------

def load_hours_state() -> tuple[dict[str, float], str | None]:
    """Returns (staff_id -> cumulative_hours, watermark date). save_hours_state
    always rewrites hours_asof for EVERY staff_id to the same as_of on every
    run, so it's a uniform watermark, not a per-staff value — safe to read
    from any single row."""
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT staff_id, cumulative_hours, hours_asof FROM staff_hours_state")
    rows = cur.fetchall()
    conn.close()
    state = {r["staff_id"]: float(r["cumulative_hours"]) for r in rows}
    watermark = str(rows[0]["hours_asof"]) if rows else None
    return state, watermark


def save_hours_state(state: dict[str, float], as_of: str):
    if not state:
        return
    import psycopg2.extras
    conn = get_connection()
    cur = get_cursor(conn)
    psycopg2.extras.execute_values(cur, """
        INSERT INTO staff_hours_state (staff_id, hours_asof, cumulative_hours)
        VALUES %s
        ON CONFLICT (staff_id) DO UPDATE SET
            hours_asof       = EXCLUDED.hours_asof,
            cumulative_hours = EXCLUDED.cumulative_hours
    """, [(staff_id, as_of, hrs) for staff_id, hrs in state.items()], page_size=1000)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def backfill_headcount(from_date: str, to_date: str):
    """One-time: full-history positions/history + full-history incentives to
    build the complete per-staff hours timeline, then emit staff_headcount_daily
    rows for [from_date, to_date]."""
    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)
    unit_ids = [u["id"] for u in units]
    unit_names = {u["id"]: u["name"] for u in units}

    headers = make_headers()
    print("Fetching full positions/history...", flush=True)
    history_rows = fetch_positions_history(unit_ids, HOURS_FLOOR, to_date, headers)
    print(f"  {len(history_rows)} rows, {len(set(r['staffId'] for r in history_rows))} distinct staff", flush=True)

    print("Fetching full incentives-by-members (quarterly chunks)...", flush=True)
    all_shifts = []
    quarters = list(quarter_ranges(HOURS_FLOOR, to_date))
    for i, (q_from, q_to) in enumerate(quarters, 1):
        headers = make_headers()
        shifts = fetch_incentive_shifts(unit_ids, q_from, q_to, headers)
        all_shifts.extend(shifts)
        print(f"  [{i}/{len(quarters)}] {q_from}..{q_to}: {len(shifts)} shifts", flush=True)

    print("Building hours timeline...", flush=True)
    timeline = build_hours_timeline(all_shifts)

    print(f"Computing headcount {from_date}..{to_date}...", flush=True)
    headcount_rows = compute_headcount_daily(history_rows, timeline, from_date, to_date, unit_names)
    upsert_headcount_daily(headcount_rows)
    print(f"  {len(headcount_rows)} headcount rows upserted", flush=True)

    # Seed the incremental-run cache with the final cumulative total as of to_date.
    final_totals = {sid: (cum[-1] if cum else 0.0) for sid, (dates, cum) in timeline.items()}
    save_hours_state(final_totals, to_date)
    print(f"staff_hours_state seeded for {len(final_totals)} staff as of {to_date}")


def daily_run(from_date: str, to_date: str):
    """Incremental: small window (typically the last few days). Advances
    staff_hours_state day by day and emits headcount for [from_date, to_date]
    using positions/history fetched just for that window (still returns full
    lifetime chains for anyone touched in it, but we only need active-roster
    membership here, not hire dates).

    staff_hours_state holds a running TOTAL, not a per-day delta — re-adding
    a day's shift hours on a second run would double-count them. Unlike the
    rest of this repo's daily jobs (which recompute+upsert a fresh aggregate
    per day, so overlapping lookback windows are naturally idempotent), this
    accumulator can only move forward. So days at/before the saved watermark
    are skipped entirely here (no hours re-added, no headcount rewritten) —
    they were already correctly written by whichever run first crossed them.
    Effectively, --from-date only matters for the very first run (before any
    watermark exists); every run after that resumes exactly where the last
    one left off, regardless of how far back --from-date asks. Trade-off:
    late-arriving corrections to old shift data won't be picked up here — a
    full --backfill re-run is the fix if that ever matters."""
    with open(UNITS_FILE, encoding="utf-8") as f:
        units = json.load(f)
    unit_ids = [u["id"] for u in units]
    unit_names = {u["id"]: u["name"] for u in units}

    state, watermark = load_hours_state()
    effective_from = max(from_date, str(date.fromisoformat(watermark) + timedelta(days=1))) if watermark else from_date
    if effective_from > to_date:
        print(f"Nothing to do — watermark ({watermark}) already covers through {to_date}")
        return

    headers = make_headers()
    history_rows = fetch_positions_history(unit_ids, effective_from, to_date, headers)

    headcount_rows = []
    d = date.fromisoformat(effective_from)
    end_d = date.fromisoformat(to_date)
    while d <= end_d:
        day = str(d)
        headers = make_headers()
        shifts = fetch_incentive_shifts(unit_ids, day, str(d + timedelta(days=1)), headers)
        for staff_id, clock_in, clock_out in shifts:
            state[staff_id] = state.get(staff_id, 0.0) + shift_hours(clock_in, clock_out)

        # Active-on-day roster from a window wide enough to cover this single day.
        # unit_names allowlists our 7 units — history_rows can include transfer
        # records into other Dodo units (see compute_headcount_daily's docstring).
        day_rows = [r for r in history_rows
                    if r["unitId"] in unit_names
                    and r["takePositionOn"] <= day and (not r["leavePositionOn"] or r["leavePositionOn"] >= day)]
        counts = defaultdict(lambda: {"novice": 0, "trainee": 0, "experienced": 0})
        for r in day_rows:
            hrs = state.get(r["staffId"], 0.0)
            counts[r["unitId"]][tier_of(hrs)] += 1
        for unit_id, tiers in counts.items():
            headcount_rows.append({
                "date": day, "unit_id": unit_id, "unit_name": unit_names.get(unit_id),
                "novice_count": tiers["novice"], "trainee_count": tiers["trainee"],
                "experienced_count": tiers["experienced"],
                "active_count": tiers["novice"] + tiers["trainee"] + tiers["experienced"],
            })
        print(f"  {day}: {len(shifts)} shifts, {len(day_rows)} active", flush=True)
        d += timedelta(days=1)

    upsert_headcount_daily(headcount_rows)
    save_hours_state(state, to_date)
    print(f"headcount: {len(headcount_rows)} rows, hours state saved as of {to_date}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="Reporting window start for headcount")
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--backfill", action="store_true",
                         help="One-time full-history run (fetches 2016-01-01.. for hours/chains)")
    args = parser.parse_args()

    if args.backfill:
        backfill_headcount(args.from_date, args.to_date)
    else:
        daily_run(args.from_date, args.to_date)


if __name__ == "__main__":
    main()
