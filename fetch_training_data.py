"""
Pull Coros training data (activities, daily physiology, sleep, workout
library, scheduled workouts) into a local SQLite DB + per-activity JSON
files for training plan design.

Reuses the auth/API client already installed for the coros-mcp tool (same
stored token, same endpoints) instead of re-implementing the Coros protocol.

Run with the coros-training-mcp tool's own Python interpreter so `coros_api`
and its dependencies (httpx, pydantic) are importable:

    "C:\\Users\\<you>\\AppData\\Roaming\\uv\\tools\\coros-training-mcp\\Scripts\\python.exe" fetch_training_data.py

Plain `python fetch_training_data.py` also works — it auto-relaunches itself
under that interpreter if the current one can't import coros_api.

Storage layout (see README.md for the full rationale). Split by function, and
further split-by-size within a function once a single file would cross
GitHub's 100MB per-file push limit:
    data/coros.db                     — SQLite: activities (summary + feel +
                                         effect scores), daily_metrics,
                                         sleep_records, workouts,
                                         scheduled_workouts, fetch_log
    data/detail/activities_YYYY.db    — full nested per-activity detail (laps,
                                         zones, device, weather...), sharded by
                                         year; a year rolls into _part2/_part3
                                         once its shard nears 50MB
    data/activities_summary.csv       — human-glance CSV (unchanged)
    data/fetch_meta.json              — last-run metadata (unchanged)
All of the above are git-tracked so a fresh clone has complete data without
needing its own COROS account.
"""

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta


def _relaunch_in_tool_venv() -> None:
    """Re-exec under the coros-training-mcp venv's Python if coros_api isn't importable here."""
    home = os.path.expanduser("~")
    if os.name == "nt":
        candidate = os.path.join(home, "AppData", "Roaming", "uv", "tools",
                                  "coros-training-mcp", "Scripts", "python.exe")
    else:
        candidate = os.path.join(home, ".local", "share", "uv", "tools",
                                  "coros-training-mcp", "bin", "python")
    if os.path.isfile(candidate) and os.path.abspath(candidate) != os.path.abspath(sys.executable):
        os.execv(candidate, [candidate, *sys.argv])
    sys.exit(
        "Could not import coros_api and could not find the coros-training-mcp "
        "tool venv to relaunch under. Run this script with that venv's python.exe, "
        "e.g.:\n"
        f'  "{candidate}" {" ".join(sys.argv)}'
    )


try:
    import coros_api
except ImportError:
    _relaunch_in_tool_venv()
    import coros_api  # unreachable after os.execv, kept for clarity


DAY_CHUNK_ACTIVITIES = 30   # activity list: known to need small windows
DAY_CHUNK_DAILY = 160       # analyse/dayDetail/query: docs say up to ~24 weeks (168d)
DAY_CHUNK_SLEEP = 90        # mobile sleep API: no documented limit, stay conservative
DAY_CHUNK_SCHEDULE = 160    # training/schedule/query: same family as daily analyse
SCHEDULE_FUTURE_DAYS = 120  # also pull already-planned future workouts
DETAIL_CONCURRENCY = 5      # parallel activity-detail requests
FULL_HISTORY_START = "20100101"  # COROS accounts don't predate this; empty chunks are cheap
DETAIL_SHARD_MAX_BYTES = 50 * 1024 * 1024  # stay under GitHub's 50MB recommended single-file size


SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    start_time INTEGER,
    end_time INTEGER,
    sport_type INTEGER,
    sport_name TEXT,
    name TEXT,
    duration_seconds INTEGER,
    distance_meters REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    calories INTEGER,
    training_load INTEGER,
    avg_power INTEGER,
    normalized_power INTEGER,
    elevation_gain INTEGER,
    feel_type INTEGER,
    sport_note TEXT,
    tired_rate REAL,
    tired_rate_state INTEGER,
    aerobic_effect REAL,
    aerobic_effect_state INTEGER,
    anaerobic_effect REAL,
    anaerobic_effect_state INTEGER,
    performance INTEGER,
    no_performance_reason TEXT,
    avg_cadence REAL,
    max_cadence REAL,
    avg_ground_time_ms REAL,
    avg_vert_ratio REAL,
    avg_vert_vibration REAL,
    avg_leg_stiffness REAL,
    avg_ground_balance_left_pct REAL,
    efficiency_factor REAL,
    vo2max REAL,
    detail_db_path TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    date TEXT PRIMARY KEY,
    avg_sleep_hrv REAL,
    baseline REAL,
    rhr INTEGER,
    training_load INTEGER,
    training_load_ratio REAL,
    tired_rate REAL,
    ati REAL,
    cti REAL,
    performance INTEGER,
    distance REAL,
    duration INTEGER,
    vo2max REAL,
    lthr INTEGER,
    ltsp INTEGER,
    stamina_level REAL,
    stamina_level_7d REAL,
    interval_list_json TEXT
);

CREATE TABLE IF NOT EXISTS sleep_records (
    date TEXT PRIMARY KEY,
    total_duration_minutes INTEGER,
    deep_minutes INTEGER,
    light_minutes INTEGER,
    rem_minutes INTEGER,
    awake_minutes INTEGER,
    nap_minutes INTEGER,
    avg_hr INTEGER,
    min_hr INTEGER,
    max_hr INTEGER,
    quality_score INTEGER
);

CREATE TABLE IF NOT EXISTS workouts (
    workout_id TEXT PRIMARY KEY,
    name TEXT,
    sport_type INTEGER,
    sport_name TEXT,
    estimated_time_seconds INTEGER,
    estimated_distance REAL,
    target_type INTEGER,
    target_value INTEGER,
    exercise_count INTEGER,
    exercises_json TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS scheduled_workouts (
    plan_id TEXT,
    id_in_plan TEXT,
    happen_day TEXT,
    workout_id TEXT,
    workout_name TEXT,
    sport_type INTEGER,
    sport_name TEXT,
    sort_no INTEGER,
    entity_json TEXT,
    workout_json TEXT,
    fetched_at TEXT,
    PRIMARY KEY (plan_id, id_in_plan)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    run_at TEXT,
    since_day TEXT,
    end_day TEXT,
    activity_count INTEGER,
    activity_detail_fetched INTEGER,
    daily_count INTEGER,
    sleep_count INTEGER,
    workout_count INTEGER,
    scheduled_count INTEGER
);
"""


def daterange_chunks(start: datetime, end: datetime, chunk_days: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        yield cur.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")
        cur = chunk_end + timedelta(days=1)


async def fetch_all_activities(auth, start_day: str, end_day: str) -> list:
    start = datetime.strptime(start_day, "%Y%m%d")
    end = datetime.strptime(end_day, "%Y%m%d")

    by_id = {}
    for chunk_start, chunk_end in daterange_chunks(start, end, DAY_CHUNK_ACTIVITIES):
        page = 1
        while True:
            activities, total = await coros_api.fetch_activities(
                auth, chunk_start, chunk_end, page=page, size=100
            )
            for a in activities:
                by_id[a.activity_id] = a
            if page * 100 >= total or not activities:
                break
            page += 1
    return sorted(by_id.values(), key=lambda a: a.start_time or "")


def _detail_shard_paths_for_year(detail_dir: str, year: int) -> list:
    """All existing shard files for `year`, in part order (part1 first)."""
    paths = []
    part = 1
    while True:
        suffix = "" if part == 1 else f"_part{part}"
        path = os.path.join(detail_dir, f"activities_{year}{suffix}.db")
        if not os.path.isfile(path):
            break
        paths.append(path)
        part += 1
    return paths


def _writable_detail_shard(detail_dir: str, year: int) -> str:
    """
    Path new rows for `year` should go into. Rolls to a new _partN file once
    the current one is at/over DETAIL_SHARD_MAX_BYTES, so no single tracked
    file ever approaches GitHub's 100MB push limit.
    """
    part = 1
    while True:
        suffix = "" if part == 1 else f"_part{part}"
        path = os.path.join(detail_dir, f"activities_{year}{suffix}.db")
        if not os.path.isfile(path) or os.path.getsize(path) < DETAIL_SHARD_MAX_BYTES:
            return path
        part += 1


def _init_detail_shard(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS activity_detail "
        "(activity_id TEXT PRIMARY KEY, start_time INTEGER, detail_json TEXT)"
    )
    return conn


async def fetch_all_details_cached(auth, activities: list, detail_dir: str, refresh_recent_days: int):
    """
    Fetch full activity detail, caching each activity in a per-year SQLite
    shard under detail_dir (see _writable_detail_shard for the >50MB rollover
    rule). Activities older than `refresh_recent_days` that are already
    cached are read back instead of re-fetched — historical activity detail
    essentially never changes, so this keeps repeat runs cheap even once the
    local history spans years. Recent activities are always re-fetched in
    case a feel rating / note gets added after the fact.

    Returns (details_by_id, detail_db_path_by_id, fetched_count).
    """
    os.makedirs(detail_dir, exist_ok=True)
    cutoff_ts = datetime.now().timestamp() - refresh_recent_days * 86400

    open_conns = {}  # shard path -> open connection, kept open for the whole run

    def _conn(path):
        if path not in open_conns:
            open_conns[path] = _init_detail_shard(path)
        return open_conns[path]

    def _year_of(activity):
        return datetime.fromtimestamp(int(activity.start_time)).year if activity.start_time else datetime.now().year

    details = {}
    detail_paths = {}
    to_fetch = []
    for a in activities:
        year = _year_of(a)
        start_ts = int(a.start_time) if a.start_time else 0
        cached_json, cached_path = None, None
        for path in _detail_shard_paths_for_year(detail_dir, year):
            row = _conn(path).execute(
                "SELECT detail_json FROM activity_detail WHERE activity_id = ?", (a.activity_id,)
            ).fetchone()
            if row is not None:
                cached_json, cached_path = row[0], path
                break
        if cached_json is not None and start_ts < cutoff_ts:
            details[a.activity_id] = json.loads(cached_json)
            detail_paths[a.activity_id] = cached_path
        else:
            to_fetch.append(a)

    sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
    fetched = {}

    async def _one(activity):
        async with sem:
            try:
                detail = await coros_api.fetch_activity_detail(
                    auth, activity.activity_id, activity.sport_type or 0
                )
            except Exception as exc:
                detail = {"_error": str(exc)}
        fetched[activity.activity_id] = detail

    await asyncio.gather(*(_one(a) for a in to_fetch))

    for a in to_fetch:
        detail = fetched[a.activity_id]
        details[a.activity_id] = detail
        path = _writable_detail_shard(detail_dir, _year_of(a))
        detail_paths[a.activity_id] = path
        _conn(path).execute(
            "INSERT OR REPLACE INTO activity_detail (activity_id, start_time, detail_json) VALUES (?,?,?)",
            (a.activity_id, int(a.start_time) if a.start_time else None, json.dumps(detail, ensure_ascii=False)),
        )

    for conn in open_conns.values():
        conn.commit()
        conn.close()

    return details, detail_paths, len(to_fetch)


async def fetch_daily_records_chunked(auth, start_day: str, end_day: str) -> list:
    start = datetime.strptime(start_day, "%Y%m%d")
    end = datetime.strptime(end_day, "%Y%m%d")
    by_date = {}
    for chunk_start, chunk_end in daterange_chunks(start, end, DAY_CHUNK_DAILY):
        for rec in await coros_api.fetch_daily_records(auth, chunk_start, chunk_end):
            by_date[rec.date] = rec
    return sorted(by_date.values(), key=lambda r: r.date)


async def fetch_sleep_chunked(auth, start_day: str, end_day: str) -> list:
    start = datetime.strptime(start_day, "%Y%m%d")
    end = datetime.strptime(end_day, "%Y%m%d")
    by_date = {}
    for chunk_start, chunk_end in daterange_chunks(start, end, DAY_CHUNK_SLEEP):
        for rec in await coros_api.fetch_sleep(auth, chunk_start, chunk_end):
            by_date[rec.date] = rec
    return sorted(by_date.values(), key=lambda r: r.date)


async def fetch_scheduled_chunked(auth, start_day: str, end_day: str) -> list:
    start = datetime.strptime(start_day, "%Y%m%d")
    end = datetime.strptime(end_day, "%Y%m%d")
    by_key = {}
    for chunk_start, chunk_end in daterange_chunks(start, end, DAY_CHUNK_SCHEDULE):
        for entry in await coros_api.fetch_scheduled_workouts(auth, chunk_start, chunk_end):
            by_key[(entry["plan_id"], entry["id_in_plan"])] = entry
    return sorted(by_key.values(), key=lambda e: e["happen_day"])


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert_activity(conn, a, detail, detail_db_path: str, fetched_at: str) -> None:
    detail = detail if isinstance(detail, dict) else {}
    summary = detail.get("summary") or {}
    feel = detail.get("sportFeelInfo") or {}
    row = {
        "activity_id": a.activity_id,
        "start_time": int(a.start_time) if a.start_time else None,
        "end_time": int(a.end_time) if a.end_time else None,
        "sport_type": a.sport_type,
        "sport_name": a.sport_name,
        "name": a.name,
        "duration_seconds": a.duration_seconds,
        "distance_meters": a.distance_meters,
        "avg_hr": a.avg_hr,
        "max_hr": a.max_hr,
        "calories": a.calories,
        "training_load": a.training_load,
        "avg_power": a.avg_power,
        "normalized_power": a.normalized_power,
        "elevation_gain": a.elevation_gain,
        "feel_type": feel.get("feelType") or None,
        "sport_note": feel.get("sportNote") or None,
        "tired_rate": summary.get("tiredRate"),
        "tired_rate_state": summary.get("tiredRateState"),
        "aerobic_effect": summary.get("aerobicEffect"),
        "aerobic_effect_state": summary.get("aerobicEffectState"),
        "anaerobic_effect": summary.get("anaerobicEffect"),
        "anaerobic_effect_state": summary.get("anaerobicEffectState"),
        "performance": summary.get("performance"),
        "no_performance_reason": summary.get("noPerformanceReasonTip") or summary.get("noPerformanceReason"),
        "avg_cadence": summary.get("avgCadence"),
        "max_cadence": summary.get("maxCadence"),
        "avg_ground_time_ms": summary.get("avgGroundTime"),
        "avg_vert_ratio": summary.get("avgVertRatio"),
        "avg_vert_vibration": summary.get("avgVertVibration"),
        "avg_leg_stiffness": summary.get("avgLegStiffness"),
        "avg_ground_balance_left_pct": summary.get("avgGroundBalanceLeft"),
        "efficiency_factor": summary.get("efficiencyFactor"),
        "vo2max": summary.get("currentVo2Max") or summary.get("hrmVo2Max"),
        "detail_db_path": detail_db_path,
        "fetched_at": fetched_at,
    }
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO activities ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
        row,
    )


def upsert_daily(conn, rec) -> None:
    d = rec.model_dump()
    interval_list = d.pop("interval_list", None)
    d["interval_list_json"] = json.dumps(interval_list) if interval_list else None
    cols = list(d.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO daily_metrics ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
        d,
    )


def upsert_sleep(conn, rec) -> None:
    d = rec.model_dump()
    phases = d.pop("phases") or {}
    d.update(phases)
    cols = list(d.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO sleep_records ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
        d,
    )


def upsert_workout(conn, w: dict, fetched_at: str) -> None:
    row = {
        "workout_id": w["id"],
        "name": w.get("name"),
        "sport_type": w.get("sport_type"),
        "sport_name": w.get("sport_name"),
        "estimated_time_seconds": w.get("estimated_time_seconds"),
        "estimated_distance": w.get("estimated_distance"),
        "target_type": w.get("target_type"),
        "target_value": w.get("target_value"),
        "exercise_count": w.get("exercise_count"),
        "exercises_json": json.dumps(w.get("exercises") or [], ensure_ascii=False),
        "fetched_at": fetched_at,
    }
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO workouts ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
        row,
    )


def upsert_scheduled(conn, entry: dict, fetched_at: str) -> None:
    row = {
        "plan_id": entry["plan_id"],
        "id_in_plan": entry["id_in_plan"],
        "happen_day": entry.get("happen_day"),
        "workout_id": entry.get("workout_id"),
        "workout_name": entry.get("workout_name"),
        "sport_type": entry.get("sport_type"),
        "sport_name": entry.get("sport_name"),
        "sort_no": entry.get("sort_no"),
        "entity_json": json.dumps(entry.get("entity") or {}, ensure_ascii=False),
        "workout_json": json.dumps(entry.get("workout") or {}, ensure_ascii=False),
        "fetched_at": fetched_at,
    }
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO scheduled_workouts ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
        row,
    )


def format_pace(distance_meters, duration_seconds) -> str:
    if not distance_meters or not duration_seconds:
        return ""
    sec_per_km = duration_seconds / (distance_meters / 1000)
    return f"{int(sec_per_km // 60)}:{int(sec_per_km % 60):02d}/km"


def write_csv(path: str, activities: list) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date", "time", "name", "sport", "duration_min", "distance_km",
            "pace", "avg_hr", "max_hr", "calories", "training_load", "elevation_gain_m",
        ])
        for a in activities:
            dt = datetime.fromtimestamp(int(a.start_time)) if a.start_time else None
            writer.writerow([
                dt.strftime("%Y-%m-%d") if dt else "",
                dt.strftime("%H:%M") if dt else "",
                a.name or "",
                a.sport_name or "",
                round((a.duration_seconds or 0) / 60, 1),
                round((a.distance_meters or 0) / 1000, 2),
                format_pace(a.distance_meters, a.duration_seconds),
                a.avg_hr or "",
                a.max_hr or "",
                a.calories or "",
                a.training_load or "",
                a.elevation_gain or "",
            ])


async def main(start_day: str, out_dir: str, refresh_recent_days: int) -> None:
    auth = coros_api.get_stored_auth()
    if auth is None:
        auth = await coros_api.try_auto_login()
    if auth is None:
        sys.exit(
            "No valid Coros auth token found (and COROS_EMAIL/COROS_PASSWORD env "
            "vars are not set for auto-login). Authenticate first, e.g. via the "
            "coros-mcp `authenticate_coros` tool."
        )

    end = datetime.now()
    end_day = end.strftime("%Y%m%d")
    schedule_end_day = (end + timedelta(days=SCHEDULE_FUTURE_DAYS)).strftime("%Y%m%d")

    os.makedirs(out_dir, exist_ok=True)
    detail_dir = os.path.join(out_dir, "detail")

    print(f"Fetching activities {start_day} - {end_day} (region={auth.region}) ...")
    activities = await fetch_all_activities(auth, start_day, end_day)
    print(f"  {len(activities)} activities found. Fetching detail (cached where possible) ...")
    details, detail_paths, fetched_count = await fetch_all_details_cached(
        auth, activities, detail_dir, refresh_recent_days
    )
    print(f"  {fetched_count} fetched over the network, {len(activities) - fetched_count} read from cache.")

    print("Fetching daily physiological metrics (training load, VO2max, RHR, HRV) ...")
    daily_records = await fetch_daily_records_chunked(auth, start_day, end_day)

    print("Fetching sleep stage data ...")
    try:
        sleep_records = await fetch_sleep_chunked(auth, start_day, end_day)
    except ValueError as exc:
        print(f"  skipped ({exc})")
        sleep_records = []

    print("Fetching workout library ...")
    workouts = await coros_api.fetch_workouts(auth)

    print(f"Fetching scheduled workouts {start_day} - {schedule_end_day} ...")
    scheduled = await fetch_scheduled_chunked(auth, start_day, schedule_end_day)

    fetched_at = datetime.now().isoformat(timespec="seconds")
    conn = init_db(os.path.join(out_dir, "coros.db"))
    with conn:
        for a in activities:
            path = detail_paths.get(a.activity_id)
            rel_path = os.path.relpath(path, out_dir).replace(os.sep, "/") if path else None
            upsert_activity(conn, a, details.get(a.activity_id), rel_path, fetched_at)
        for rec in daily_records:
            upsert_daily(conn, rec)
        for rec in sleep_records:
            upsert_sleep(conn, rec)
        for w in workouts:
            upsert_workout(conn, w, fetched_at)
        for entry in scheduled:
            upsert_scheduled(conn, entry, fetched_at)
        conn.execute(
            "INSERT INTO fetch_log (run_at, since_day, end_day, activity_count, "
            "activity_detail_fetched, daily_count, sleep_count, workout_count, scheduled_count) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (fetched_at, start_day, end_day, len(activities), fetched_count,
             len(daily_records), len(sleep_records), len(workouts), len(scheduled)),
        )
    conn.close()

    write_csv(os.path.join(out_dir, "activities_summary.csv"), activities)

    meta = {
        "generated_at": fetched_at,
        "start_day": start_day,
        "end_day": end_day,
        "region": auth.region,
        "activity_count": len(activities),
        "activity_detail_fetched": fetched_count,
        "daily_record_count": len(daily_records),
        "sleep_record_count": len(sleep_records),
        "workout_count": len(workouts),
        "scheduled_count": len(scheduled),
    }
    with open(os.path.join(out_dir, "fetch_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(
        f"Done. {len(activities)} activities, {len(daily_records)} daily records, "
        f"{len(sleep_records)} sleep records, {len(workouts)} workouts, "
        f"{len(scheduled)} scheduled entries -> {out_dir}/coros.db"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    range_group = parser.add_mutually_exclusive_group()
    range_group.add_argument("--months", type=int, default=None,
                              help="How many months back to fetch (default 4 if no other range given)")
    range_group.add_argument("--since", default=None, help="Start date as YYYYMMDD")
    range_group.add_argument("--full-history", action="store_true",
                              help=f"Fetch entire account history (since {FULL_HISTORY_START})")
    parser.add_argument("--out-dir", default="data", help="Output directory (default ./data)")
    parser.add_argument("--refresh-recent-days", type=int, default=14,
                         help="Always re-fetch (not read from cache) activity detail this recent, "
                              "in case a feel rating/note was added after the fact (default 14)")
    args = parser.parse_args()

    if args.full_history:
        start_day = FULL_HISTORY_START
    elif args.since:
        start_day = args.since
    else:
        start = datetime.now() - timedelta(days=30 * (args.months or 4))
        start_day = start.strftime("%Y%m%d")

    asyncio.run(main(start_day, args.out_dir, args.refresh_recent_days))
