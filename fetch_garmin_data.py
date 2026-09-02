#!/usr/bin/env python3
# /// script
# dependencies = ["garminconnect"]
# ///
"""
Pull Garmin Connect activity/wellness/sleep history from *before* this
runner switched to Coros (Coros device activation date: 2021-06-25, see
`data/coros.db`) into a local SQLite DB + per-activity JSON files, mirroring
`fetch_training_data.py`'s storage design (see README.md) but kept in a
separate DB since Coros- and Garmin-specific fields don't line up 1:1.

Run with uv -- it installs the `garminconnect` dependency declared above
into an ephemeral environment automatically, no venv setup needed:

    uv run fetch_garmin_data.py

First run needs Garmin Connect credentials via the GARMIN_EMAIL /
GARMIN_PASSWORD env vars. If the account has MFA enabled, this prompts for
a one-time code interactively -- that step has to be run by a human in an
interactive terminal once; after that garminconnect caches the session
under ~/.garminconnect/ and later runs won't need credentials or MFA again.

Storage layout:
    data/garmin.db                          -- SQLite: activities (summary +
                                                full raw_json), daily_wellness,
                                                sleep_records, fetch_log
    data/garmin_detail/activities_YYYY.db   -- full per-activity detail
                                                (splits/laps + full per-second
                                                GPS/sensor time series from
                                                get_activity_details -- this is
                                                NOT a lightweight summary, it's
                                                comparable in size to a FIT file;
                                                averages ~680KB/activity, kept
                                                intentionally), sharded by year;
                                                a year rolls into _part2/_part3
                                                once its shard nears 50MB
    data/garmin_activities_summary.csv      -- human-glance CSV
    data/garmin_fetch_meta.json             -- last-run metadata

Scope notes:
  - Only fetches the window BEFORE Coros started (default through
    2021-06-24) to avoid overlap with data/coros.db.
  - Daily wellness/sleep is only pulled for days that have a logged
    activity, not every calendar day in range. Unlike Coros's chunked
    multi-day physiology endpoints, Garmin's wellness endpoints are one
    request per day, and pulling ~7 years of empty rest days would be a lot
    of API calls for little training-relevant signal.
  - Raw API responses are always kept in full (raw_json / detail_json
    columns) alongside the extracted columns, since the exact field names
    in Garmin's JSON weren't verified against a live account while writing
    this script -- if a column mapping is slightly off, re-deriving it from
    raw_json later doesn't require re-fetching from Garmin.
  - Original FIT/GPX file export is not implemented here (same as Coros's
    current known limitation, see README.md) -- but note that
    get_activity_details/get_activity_splits already return full per-second
    GPS/HR/pace samples, so data/garmin_detail/ ends up similarly large to
    what a FIT export would be (~1GB+ across the full history window). This
    was a deliberate choice to keep, not an oversight -- see README.md.
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass  # stdout isn't reconfigurable (e.g. redirected on some platforms) -- progress just won't be live

try:
    import garminconnect
except ImportError:
    sys.exit(
        "garminconnect is not installed. Run this script with uv so the PEP 723 "
        "inline dependency above is installed automatically:\n"
        "    uv run fetch_garmin_data.py\n"
        "(or `pip install garminconnect` into whatever interpreter you're using)."
    )

from garmin_auth import get_client


GARMIN_HISTORY_START = "20140101"  # generous lower bound (Forerunner 620 released 2013); empty windows are cheap
GARMIN_FETCH_END_DAY = "20210624"  # day before Coros device activation (2021-06-25, see data/coros.db) -- avoids overlap
DETAIL_SHARD_MAX_BYTES = 50 * 1024 * 1024  # stay under GitHub's 50MB recommended single-file size
REQUEST_PAUSE_SECONDS = 0.3  # be polite between per-activity/per-day requests
RATE_LIMIT_BACKOFF_SECONDS = 30


SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    start_time INTEGER,
    sport_type TEXT,
    name TEXT,
    duration_seconds REAL,
    distance_meters REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    calories INTEGER,
    elevation_gain REAL,
    avg_cadence REAL,
    vo2max REAL,
    training_load REAL,
    detail_db_path TEXT,
    raw_json TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_wellness (
    date TEXT PRIMARY KEY,
    steps INTEGER,
    resting_hr INTEGER,
    body_battery_high INTEGER,
    body_battery_low INTEGER,
    stress_avg INTEGER,
    vo2max REAL,
    raw_json TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS sleep_records (
    date TEXT PRIMARY KEY,
    total_duration_minutes INTEGER,
    deep_minutes INTEGER,
    light_minutes INTEGER,
    rem_minutes INTEGER,
    awake_minutes INTEGER,
    avg_hr INTEGER,
    quality_score INTEGER,
    raw_json TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS fetch_log (
    run_at TEXT,
    since_day TEXT,
    end_day TEXT,
    activity_count INTEGER,
    activity_detail_fetched INTEGER,
    wellness_count INTEGER,
    sleep_count INTEGER
);
"""


def _call_with_retry(fn, *args, **kwargs):
    delay = RATE_LIMIT_BACKOFF_SECONDS
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except garminconnect.GarminConnectTooManyRequestsError:
            print(f"    rate limited, backing off {delay}s ...")
            time.sleep(delay)
            delay *= 2
    return fn(*args, **kwargs)  # let the final attempt's error surface


def _parse_start_dt(activity: dict):
    for key in ("startTimeGMT", "startTimeLocal"):
        raw = activity.get(key)
        if not raw:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    return None


def fetch_all_activities(client, start_day: str, end_day: str) -> list:
    start = datetime.strptime(start_day, "%Y%m%d").strftime("%Y-%m-%d")
    end = datetime.strptime(end_day, "%Y%m%d").strftime("%Y-%m-%d")
    activities = _call_with_retry(client.get_activities_by_date, start, end) or []
    return sorted(activities, key=lambda a: (_parse_start_dt(a) or datetime.min))


def _detail_shard_paths_for_year(detail_dir: str, year: int) -> list:
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


SPLITS_DB_FILENAME = "splits.db"  # small (~11KB/activity) lap/split summaries, kept
                                   # separate from the year shards' full per-second
                                   # GPS/sensor time series so lap-level queries don't
                                   # have to touch the ~1GB detail store (see README.md)


def _init_splits_db(detail_dir: str) -> sqlite3.Connection:
    path = os.path.join(detail_dir, SPLITS_DB_FILENAME)
    os.makedirs(detail_dir, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS activity_splits "
        "(activity_id TEXT PRIMARY KEY, start_time INTEGER, splits_json TEXT)"
    )
    return conn


def fetch_all_details_cached(client, activities: list, detail_dir: str, out_dir: str, garmin_conn, fetched_at: str) -> int:
    """
    Fetch full per-activity detail (splits/laps + full per-second GPS/sensor
    time series), caching each activity in a per-year SQLite shard under
    detail_dir, then immediately upsert + commit the activity's summary row
    into garmin_conn. The lightweight `splits` portion is also mirrored into
    a small standalone splits.db so lap-level queries don't need to touch
    the much larger year shards. Both the shard and garmin_conn are
    committed right after each activity, not batched until the end, so an
    interrupted run only loses the one activity in flight -- everything
    committed so far is safe, and a re-run picks up where it left off
    (skips any activity_id already present in `activities`).
    """
    os.makedirs(detail_dir, exist_ok=True)
    open_conns = {}
    splits_conn = _init_splits_db(detail_dir)

    def _conn(path):
        if path not in open_conns:
            open_conns[path] = _init_detail_shard(path)
        return open_conns[path]

    fetched_count = 0
    total = len(activities)
    try:
        for i, a in enumerate(activities, start=1):
            activity_id = str(a.get("activityId"))
            start_dt = _parse_start_dt(a)
            year = start_dt.year if start_dt else datetime.now().year

            already_done = garmin_conn.execute(
                "SELECT 1 FROM activities WHERE activity_id = ?", (activity_id,)
            ).fetchone()
            if already_done is not None:
                continue

            cached_path = None
            for path in _detail_shard_paths_for_year(detail_dir, year):
                row = _conn(path).execute(
                    "SELECT 1 FROM activity_detail WHERE activity_id = ?", (activity_id,)
                ).fetchone()
                if row is not None:
                    cached_path = path
                    break

            if cached_path is None:
                label = start_dt.strftime("%Y-%m-%d") if start_dt else activity_id
                print(f"  [{i}/{total}] fetching detail for {label} ...")
                try:
                    details = _call_with_retry(client.get_activity_details, activity_id)
                except Exception as exc:
                    details = {"_error": str(exc)}
                try:
                    splits = _call_with_retry(client.get_activity_splits, activity_id)
                except Exception as exc:
                    splits = {"_error": str(exc)}
                time.sleep(REQUEST_PAUSE_SECONDS)

                cached_path = _writable_detail_shard(detail_dir, year)
                shard_conn = _conn(cached_path)
                shard_conn.execute(
                    "INSERT OR REPLACE INTO activity_detail (activity_id, start_time, detail_json) VALUES (?,?,?)",
                    (
                        activity_id,
                        int(start_dt.timestamp()) if start_dt else None,
                        json.dumps({"details": details, "splits": splits}, ensure_ascii=False),
                    ),
                )
                shard_conn.commit()
                splits_conn.execute(
                    "INSERT OR REPLACE INTO activity_splits (activity_id, start_time, splits_json) VALUES (?,?,?)",
                    (
                        activity_id,
                        int(start_dt.timestamp()) if start_dt else None,
                        json.dumps(splits, ensure_ascii=False),
                    ),
                )
                splits_conn.commit()
                fetched_count += 1

            rel_path = os.path.relpath(cached_path, out_dir).replace(os.sep, "/")
            upsert_activity(garmin_conn, a, rel_path, fetched_at)
            garmin_conn.commit()
    finally:
        for conn in open_conns.values():
            conn.close()
        splits_conn.close()

    return fetched_count


def fetch_wellness_and_sleep(client, activities: list, conn, fetched_at: str):
    """
    Pull daily wellness stats + sleep only for days that have a logged
    activity (see module docstring for why -- Garmin's wellness endpoints
    are one request per day, unlike Coros's chunked bulk queries). Each
    day's rows are upserted + committed immediately, and a day already
    present is skipped, so an interrupted run resumes without re-fetching.
    Returns (new_wellness_count, new_sleep_count) fetched *this run*.
    """
    days = sorted({dt.strftime("%Y-%m-%d") for dt in (_parse_start_dt(a) for a in activities) if dt})

    new_wellness = 0
    new_sleep = 0
    total = len(days)
    for i, day in enumerate(days, start=1):
        has_wellness = conn.execute("SELECT 1 FROM daily_wellness WHERE date = ?", (day,)).fetchone() is not None
        has_sleep = conn.execute("SELECT 1 FROM sleep_records WHERE date = ?", (day,)).fetchone() is not None
        if has_wellness and has_sleep:
            continue
        print(f"  [{i}/{total}] {day} ...")

        if not has_wellness:
            try:
                stats = _call_with_retry(client.get_stats, day) or {}
            except Exception as exc:
                print(f"    wellness fetch failed for {day}: {exc}")
                stats = {}
            try:
                max_metrics = _call_with_retry(client.get_max_metrics, day)
            except Exception:
                max_metrics = None
            time.sleep(REQUEST_PAUSE_SECONDS)

            vo2max = None
            if isinstance(max_metrics, list) and max_metrics:
                vo2max = (max_metrics[0].get("generic") or {}).get("vo2MaxPreciseValue") or (
                    max_metrics[0].get("generic") or {}
                ).get("vo2MaxValue")

            if stats:
                upsert_wellness(
                    conn,
                    {
                        "date": day,
                        "steps": stats.get("totalSteps"),
                        "resting_hr": stats.get("restingHeartRate"),
                        "body_battery_high": stats.get("bodyBatteryHighestValue") or stats.get("bodyBatteryMostRecentValue"),
                        "body_battery_low": stats.get("bodyBatteryLowestValue"),
                        "stress_avg": stats.get("averageStressLevel"),
                        "vo2max": vo2max,
                        "raw_json": json.dumps({"stats": stats, "max_metrics": max_metrics}, ensure_ascii=False),
                    },
                    fetched_at,
                )
                conn.commit()
                new_wellness += 1

        if not has_sleep:
            try:
                sleep = _call_with_retry(client.get_sleep_data, day)
            except Exception as exc:
                print(f"    sleep fetch failed for {day}: {exc}")
                sleep = None
            time.sleep(REQUEST_PAUSE_SECONDS)

            daily_sleep = (sleep or {}).get("dailySleepDTO") or {}
            if daily_sleep.get("sleepTimeSeconds"):
                upsert_sleep(
                    conn,
                    {
                        "date": day,
                        "total_duration_minutes": round((daily_sleep.get("sleepTimeSeconds") or 0) / 60),
                        "deep_minutes": round((daily_sleep.get("deepSleepSeconds") or 0) / 60),
                        "light_minutes": round((daily_sleep.get("lightSleepSeconds") or 0) / 60),
                        "rem_minutes": round((daily_sleep.get("remSleepSeconds") or 0) / 60),
                        "awake_minutes": round((daily_sleep.get("awakeSleepSeconds") or 0) / 60),
                        "avg_hr": (sleep or {}).get("avgSleepStress") if isinstance((sleep or {}).get("avgSleepStress"), int) else None,
                        "quality_score": ((sleep or {}).get("sleepScores") or {}).get("overall", {}).get("value"),
                        "raw_json": json.dumps(sleep, ensure_ascii=False),
                    },
                    fetched_at,
                )
                conn.commit()
                new_sleep += 1

    return new_wellness, new_sleep


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def upsert_activity(conn, a: dict, detail_db_path, fetched_at: str) -> None:
    start_dt = _parse_start_dt(a)
    activity_type = (a.get("activityType") or {}).get("typeKey")
    row = {
        "activity_id": str(a.get("activityId")),
        "start_time": int(start_dt.timestamp()) if start_dt else None,
        "sport_type": activity_type,
        "name": a.get("activityName"),
        "duration_seconds": a.get("duration"),
        "distance_meters": a.get("distance"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "calories": a.get("calories"),
        "elevation_gain": a.get("elevationGain"),
        "avg_cadence": a.get("averageRunningCadenceInStepsPerMinute"),
        "vo2max": a.get("vO2MaxValue"),
        "training_load": a.get("activityTrainingLoad") or a.get("aerobicTrainingEffect"),
        "detail_db_path": detail_db_path,
        "raw_json": json.dumps(a, ensure_ascii=False),
        "fetched_at": fetched_at,
    }
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO activities ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
        row,
    )


def upsert_wellness(conn, row: dict, fetched_at: str) -> None:
    row = {**row, "fetched_at": fetched_at}
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO daily_wellness ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
        row,
    )


def upsert_sleep(conn, row: dict, fetched_at: str) -> None:
    row = {**row, "fetched_at": fetched_at}
    cols = list(row.keys())
    conn.execute(
        f"INSERT OR REPLACE INTO sleep_records ({','.join(cols)}) VALUES ({','.join(':' + c for c in cols)})",
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
        writer.writerow(
            ["date", "time", "name", "sport", "duration_min", "distance_km", "pace", "avg_hr", "max_hr", "calories"]
        )
        for a in activities:
            dt = _parse_start_dt(a)
            writer.writerow(
                [
                    dt.strftime("%Y-%m-%d") if dt else "",
                    dt.strftime("%H:%M") if dt else "",
                    a.get("activityName") or "",
                    (a.get("activityType") or {}).get("typeKey") or "",
                    round((a.get("duration") or 0) / 60, 1),
                    round((a.get("distance") or 0) / 1000, 2),
                    format_pace(a.get("distance"), a.get("duration")),
                    a.get("averageHR") or "",
                    a.get("maxHR") or "",
                    a.get("calories") or "",
                ]
            )


def main(start_day: str, end_day: str, out_dir: str) -> None:
    client = get_client()

    os.makedirs(out_dir, exist_ok=True)
    detail_dir = os.path.join(out_dir, "garmin_detail")
    fetched_at = datetime.now().isoformat(timespec="seconds")

    # Opened once up front and committed incrementally throughout (see
    # fetch_all_details_cached / fetch_wellness_and_sleep) so an interrupted
    # run keeps everything it already fetched instead of losing it all --
    # re-running with the same range just picks up where it left off.
    conn = init_db(os.path.join(out_dir, "garmin.db"))

    print(f"Fetching Garmin activities {start_day} - {end_day} ...")
    activities = fetch_all_activities(client, start_day, end_day)
    print(f"  {len(activities)} activities found. Fetching detail (cached where possible) ...")
    fetched_count = fetch_all_details_cached(client, activities, detail_dir, out_dir, conn, fetched_at)
    print(f"  {fetched_count} fetched over the network, {len(activities) - fetched_count} already stored.")

    print("Fetching daily wellness + sleep for days with a logged activity ...")
    new_wellness, new_sleep = fetch_wellness_and_sleep(client, activities, conn, fetched_at)

    total_wellness = conn.execute("SELECT COUNT(*) FROM daily_wellness").fetchone()[0]
    total_sleep = conn.execute("SELECT COUNT(*) FROM sleep_records").fetchone()[0]

    conn.execute(
        "INSERT INTO fetch_log (run_at, since_day, end_day, activity_count, "
        "activity_detail_fetched, wellness_count, sleep_count) VALUES (?,?,?,?,?,?,?)",
        (fetched_at, start_day, end_day, len(activities), fetched_count, new_wellness, new_sleep),
    )
    conn.commit()
    conn.close()

    write_csv(os.path.join(out_dir, "garmin_activities_summary.csv"), activities)

    meta = {
        "generated_at": fetched_at,
        "start_day": start_day,
        "end_day": end_day,
        "activity_count": len(activities),
        "activity_detail_fetched_this_run": fetched_count,
        "wellness_count_total": total_wellness,
        "sleep_count_total": total_sleep,
    }
    with open(os.path.join(out_dir, "garmin_fetch_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(
        f"Done. {len(activities)} activities in range, {total_wellness} wellness days "
        f"({new_wellness} new), {total_sleep} sleep records ({new_sleep} new) -> {out_dir}/garmin.db"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since", default=GARMIN_HISTORY_START, help=f"Start date as YYYYMMDD (default {GARMIN_HISTORY_START})"
    )
    parser.add_argument(
        "--until",
        default=GARMIN_FETCH_END_DAY,
        help=f"End date as YYYYMMDD (default {GARMIN_FETCH_END_DAY}, the day before Coros device activation)",
    )
    parser.add_argument("--out-dir", default="data", help="Output directory (default ./data)")
    args = parser.parse_args()

    main(args.since, args.until, args.out_dir)
