"""
Aggregate the raw Garmin pull (data/garmin.db: activities + daily_wellness +
sleep_records tables) into compact weekly/monthly summaries + flagged
long-run and quality-session lists, mirroring analyze_training.py's design
for the pre-Coros era.

Writes results both as data/garmin_analysis/*.json (small, git-tracked) and
as tables/views back in data/garmin.db (sessions, weekly_summary,
monthly_summary, weekly_physiology, long_runs, quality_sessions) so the
git-tracked garmin.db is a complete, queryable single file -- same pattern
as analyze_training.py, just pointed at the Garmin-era DB and its own
(narrower) set of available fields.

Known gaps vs the Coros analysis (see fetch_garmin_data.py's docstring for
why): most days in daily_wellness only have vo2max populated -- steps/
resting_hr/body_battery/stress are null for the FR620/Fenix3 era, and
sleep_records is expected to be near-empty (those devices predate wrist
sleep tracking). This isn't a bug, it mirrors what fetch_garmin_data.py
actually retrieved.

Run with any Python 3 that has no extra deps (stdlib only, incl. sqlite3):
    python analyze_garmin_data.py
"""

import json
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

DATA_DIR = "data"
OUT_DIR = "data/garmin_analysis"
DB_PATH = os.path.join(DATA_DIR, "garmin.db")

RUNNING_SPORTS = {"running", "treadmill_running", "trail_running"}
QUALITY_KEYWORDS = ["間歇", "速度", "閾", "混氧", "比賽", "節奏", "interval", "tempo", "threshold", "race", "speed"]
LONG_RUN_SECONDS = 90 * 60


def week_start(dt: datetime) -> str:
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def pace_str(distance_m, duration_s):
    if not distance_m or not duration_s:
        return None
    sec_per_km = duration_s / (distance_m / 1000)
    return f"{int(sec_per_km // 60)}:{int(sec_per_km % 60):02d}"


def load_table(conn, table, order_by):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}")]


def _sql_type(value):
    if isinstance(value, bool) or isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def write_table(conn, name, rows):
    """Replace `name` with a fresh table holding `rows` (list of flat dicts, uniform shape)."""
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    if not rows:
        return
    columns = list(rows[0].keys())
    types = {}
    for col in columns:
        types[col] = "TEXT"
        for r in rows:
            if r.get(col) is not None:
                types[col] = _sql_type(r[col])
                break
    col_defs = ", ".join(f'"{c}" {types[c]}' for c in columns)
    conn.execute(f"CREATE TABLE {name} ({col_defs})")
    placeholders = ", ".join("?" for _ in columns)
    col_list = ", ".join(f'"{c}"' for c in columns)
    conn.executemany(
        f"INSERT INTO {name} ({col_list}) VALUES ({placeholders})",
        [tuple(r.get(c) for c in columns) for r in rows],
    )


def main():
    conn = sqlite3.connect(DB_PATH)
    activities = load_table(conn, "activities", "start_time")
    daily = load_table(conn, "daily_wellness", "date")

    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- per-session enriched records -------------------------------------
    sessions = []
    for a in activities:
        if not a.get("start_time"):
            continue
        if (a.get("duration_seconds") or 0) < 180 and a.get("sport_type") in RUNNING_SPORTS:
            continue
        dt = datetime.fromtimestamp(int(a["start_time"]))
        is_quality = any(k in (a.get("name") or "") for k in QUALITY_KEYWORDS)
        rec = {
            "activity_id": a["activity_id"],
            "date": dt.strftime("%Y-%m-%d"),
            "week": week_start(dt),
            "month": dt.strftime("%Y-%m"),
            "name": a.get("name"),
            "sport": a.get("sport_type"),
            "duration_min": round((a.get("duration_seconds") or 0) / 60, 1),
            "distance_km": round((a.get("distance_meters") or 0) / 1000, 2),
            "pace_per_km": pace_str(a.get("distance_meters"), a.get("duration_seconds")),
            "avg_hr": a.get("avg_hr"),
            "training_load": a.get("training_load"),
            "elevation_gain_m": a.get("elevation_gain"),
            "is_long_run": (a.get("duration_seconds") or 0) > LONG_RUN_SECONDS
                           and a.get("sport_type") in RUNNING_SPORTS,
            "is_quality": is_quality and a.get("sport_type") in RUNNING_SPORTS,
            "avg_cadence": a.get("avg_cadence"),
            "vo2max": a.get("vo2max"),
        }
        sessions.append(rec)
    sessions.sort(key=lambda r: r["date"])

    with open(os.path.join(OUT_DIR, "sessions.json"), "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

    # ---- weekly aggregation --------------------------------------------
    weekly = defaultdict(lambda: {
        "run_km": 0.0, "run_sessions": 0, "run_duration_min": 0.0,
        "long_run_count": 0, "quality_count": 0,
        "training_load_sum": 0, "cadence_samples": [],
    })
    for r in sessions:
        w = weekly[r["week"]]
        if r["sport"] in RUNNING_SPORTS:
            w["run_km"] += r["distance_km"]
            w["run_sessions"] += 1
            w["run_duration_min"] += r["duration_min"]
            if r["is_long_run"]:
                w["long_run_count"] += 1
            if r["is_quality"]:
                w["quality_count"] += 1
            if r["avg_cadence"]:
                w["cadence_samples"].append(r["avg_cadence"])
        if r["training_load"]:
            w["training_load_sum"] += r["training_load"]

    weekly_out = []
    for wk in sorted(weekly):
        w = weekly[wk]
        weekly_out.append({
            "week_start": wk,
            "run_km": round(w["run_km"], 1),
            "run_sessions": w["run_sessions"],
            "run_duration_min": round(w["run_duration_min"], 1),
            "long_run_count": w["long_run_count"],
            "quality_count": w["quality_count"],
            "training_load_sum": round(w["training_load_sum"], 1),
            "avg_cadence": round(statistics.mean(w["cadence_samples"]), 1) if w["cadence_samples"] else None,
        })
    with open(os.path.join(OUT_DIR, "weekly_summary.json"), "w", encoding="utf-8") as f:
        json.dump(weekly_out, f, ensure_ascii=False, indent=2)

    # ---- monthly totals ---------------------------------------------------
    monthly = defaultdict(lambda: {"run_km": 0.0, "run_sessions": 0, "training_load_sum": 0})
    for r in sessions:
        m = monthly[r["month"]]
        if r["sport"] in RUNNING_SPORTS:
            m["run_km"] += r["distance_km"]
            m["run_sessions"] += 1
        if r["training_load"]:
            m["training_load_sum"] += r["training_load"]
    monthly_out = [{"month": m, **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in vals.items()}}
                   for m, vals in sorted(monthly.items())]
    with open(os.path.join(OUT_DIR, "monthly_summary.json"), "w", encoding="utf-8") as f:
        json.dump(monthly_out, f, ensure_ascii=False, indent=2)

    # ---- flagged session lists --------------------------------------------
    long_runs = [r for r in sessions if r["is_long_run"]]
    quality_sessions = [r for r in sessions if r["is_quality"]]
    with open(os.path.join(OUT_DIR, "long_runs.json"), "w", encoding="utf-8") as f:
        json.dump(long_runs, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "quality_sessions.json"), "w", encoding="utf-8") as f:
        json.dump(quality_sessions, f, ensure_ascii=False, indent=2)

    # ---- daily wellness: weekly avg (mostly vo2max -- see module docstring) ----
    weekly_phys = defaultdict(lambda: {"vo2max_samples": [], "rhr_samples": [], "steps_samples": []})
    for d in daily:
        try:
            dt = datetime.strptime(d["date"], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        wp = weekly_phys[week_start(dt)]
        if d.get("vo2max") is not None:
            wp["vo2max_samples"].append(d["vo2max"])
        if d.get("resting_hr") is not None:
            wp["rhr_samples"].append(d["resting_hr"])
        if d.get("steps") is not None:
            wp["steps_samples"].append(d["steps"])

    weekly_phys_out = []
    for wk in sorted(weekly_phys):
        wp = weekly_phys[wk]
        weekly_phys_out.append({
            "week_start": wk,
            "avg_vo2max": round(statistics.mean(wp["vo2max_samples"]), 1) if wp["vo2max_samples"] else None,
            "avg_rhr": round(statistics.mean(wp["rhr_samples"]), 1) if wp["rhr_samples"] else None,
            "avg_steps": round(statistics.mean(wp["steps_samples"]), 0) if wp["steps_samples"] else None,
        })
    with open(os.path.join(OUT_DIR, "weekly_physiology.json"), "w", encoding="utf-8") as f:
        json.dump(weekly_phys_out, f, ensure_ascii=False, indent=2)

    # ---- mirror the same outputs into garmin.db ----------------------------
    with conn:
        write_table(conn, "sessions", sessions)
        write_table(conn, "weekly_summary", weekly_out)
        write_table(conn, "monthly_summary", monthly_out)
        write_table(conn, "weekly_physiology", weekly_phys_out)
        conn.execute("DROP VIEW IF EXISTS long_runs")
        conn.execute("CREATE VIEW long_runs AS SELECT * FROM sessions WHERE is_long_run")
        conn.execute("DROP VIEW IF EXISTS quality_sessions")
        conn.execute("CREATE VIEW quality_sessions AS SELECT * FROM sessions WHERE is_quality")
    conn.close()

    print(f"Sessions: {len(sessions)}  Weeks: {len(weekly_out)}  Months: {len(monthly_out)}")
    print(f"Long runs (>90min): {len(long_runs)}  Quality sessions: {len(quality_sessions)}")
    print(f"Wrote analysis files to {OUT_DIR}/ and mirrored into {DB_PATH}")


if __name__ == "__main__":
    main()
