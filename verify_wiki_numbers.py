"""
Re-derive every wiki number that came from local data, and diff it against what
the wiki actually says.

Why this exists
---------------
On 2026-08-16 a full audit found that three pace columns in
`wiki/concepts/long-runs-and-quality-sessions.md` had been filled with
placeholder values (`5:00/km` four times, `4:00/km` three times) that were never
in the source data, plus a `good_day` series that silently switched denominators
mid-paragraph. The source JSON was clean every time — the numbers were wrong
only where a page *summarised* the data by hand.

The repo already solved this exact class of bug once: `create_workouts.py` reads
a workout back from COROS and refuses to schedule it unless `verify()` passes.
This is the same gate for the wiki. Aggregates that no script re-derives are
aggregates nobody checks.

Usage
-----
    python3 verify_wiki_numbers.py            # report drift, exit 1 if any
    python3 verify_wiki_numbers.py --list     # show registered checkers
    python3 verify_wiki_numbers.py --quiet    # only print problems

Only stdlib is used, so this runs under any python3 — no venv needed (unlike
`fetch_training_data.py`, which needs the coros-training-mcp interpreter).

How a page opts in
------------------
Put an anchor comment immediately above a table:

    <!-- derived: long_runs_by_year src=data/analysis/long_runs.json -->

    | 年 | 次數 | ... |

or above a paragraph holding inline numbers:

    <!-- derived-scalars: coros_data_volume src=data/coros.db -->

    **目前資料量**：2729 筆活動、1880 天生理紀錄、...

`src=` is documentation for the reader; the checker function decides what it
actually reads. A checker with no anchor anywhere is reported as unused, and an
anchor with no checker is reported as unknown — both are drift.

Comparison is numeric where possible, so `32.5`, `32.5 km` and `**32.5 km**`
all compare equal. Paces compare as `m:ss` strings.
"""

import argparse
import collections
import json
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
WIKI = os.path.join(ROOT, "wiki")

CHECKERS = {}


def checker(name, src, kind="table"):
    def register(fn):
        CHECKERS[name] = {"name": name, "src": src, "kind": kind, "fn": fn}
        return fn

    return register


# --------------------------------------------------------------------------
# loading source data
# --------------------------------------------------------------------------

_cache = {}


def load_json(relpath):
    if relpath not in _cache:
        with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
            _cache[relpath] = json.load(handle)
    return _cache[relpath]


def coros_db():
    if "db" not in _cache:
        _cache["db"] = sqlite3.connect(os.path.join(ROOT, "data", "coros.db"))
    return _cache["db"]


def pace_seconds(text):
    """'5:23' -> 323.0. The stored pace_per_km is m:ss, not a speed (see
    wiki/concepts/coros-unit-pitfall.md — COROS field names lie about units)."""
    minutes, seconds = str(text).split(":")
    return int(minutes) * 60 + float(seconds)


def fmt_pace(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def mean(values):
    return sum(values) / len(values)


def by_year(rows, key=lambda r: str(r["date"])[:4]):
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


# --------------------------------------------------------------------------
# checkers — each returns {"key_re": ..., "rows": {key: {column: expected}}}
# for tables, or {"values": [(label, number), ...]} for scalars.
# --------------------------------------------------------------------------


@checker("long_runs_by_year", src="data/analysis/long_runs.json")
def _long_runs_by_year(_):
    rows = {}
    for year, runs in by_year(load_json("data/analysis/long_runs.json")).items():
        rows[year] = {
            "次數": len(runs),
            "平均距離": round(mean([r["distance_km"] for r in runs]), 1),
            "最長": round(max(r["distance_km"] for r in runs), 1),
            "平均時長": round(mean([r["duration_min"] for r in runs])),
            "平均配速": fmt_pace(mean([pace_seconds(r["pace_per_km"]) for r in runs])),
            "≥30km": sum(1 for r in runs if r["distance_km"] >= 30),
            "≥35km": sum(1 for r in runs if r["distance_km"] >= 35),
        }
    return {"key_re": r"(20\d\d)", "rows": rows}


@checker("long_runs_2026_longest", src="data/analysis/long_runs.json")
def _long_runs_2026_longest(_):
    runs = [r for r in load_json("data/analysis/long_runs.json") if str(r["date"]).startswith("2026")]
    runs.sort(key=lambda r: -r["distance_km"])
    rows = {}
    for run in runs[:6]:
        rows[str(run["date"])[:10]] = {
            "距離": round(run["distance_km"], 1),
            "時長": round(run["duration_min"]),
            "配速": run["pace_per_km"],
            "平均HR": run["avg_hr"],
            "爬升": round(run["elevation_gain_m"] or 0),
        }
    return {"key_re": r"(\d{4}-\d{2}-\d{2})", "rows": rows}


@checker("quality_sessions_by_year", src="data/analysis/quality_sessions.json")
def _quality_sessions_by_year(_):
    rows = {}
    for year, sessions in by_year(load_json("data/analysis/quality_sessions.json")).items():
        rows[year] = {
            "偵測到次數": len(sessions),
            "平均距離": round(mean([s["distance_km"] for s in sessions]), 1),
            "平均配速": fmt_pace(mean([pace_seconds(s["pace_per_km"]) for s in sessions])),
        }
    return {"key_re": r"(20\d\d)", "rows": rows}


@checker("merged_long_quality", src="data/analysis/long_runs.json + quality_sessions.json")
def _merged_long_quality(_):
    quality = {s["activity_id"] for s in load_json("data/analysis/quality_sessions.json")}
    merged = [r for r in load_json("data/analysis/long_runs.json") if r["activity_id"] in quality]
    merged.sort(key=lambda r: r["date"])
    rows = {
        str(r["date"])[:10]: {
            "距離": round(r["distance_km"], 1),
            "時長": round(r["duration_min"]),
        }
        for r in merged
    }
    return {"key_re": r"(\d{4}-\d{2}-\d{2})", "rows": rows}


def _monthly_rollup(relpath):
    return {r["month"]: r for r in load_json(relpath)}


@checker("monthly_2026", src="data/analysis/monthly_summary.json")
def _monthly_2026(_):
    months = {m: r for m, r in _monthly_rollup("data/analysis/monthly_summary.json").items()
              if m.startswith("2026")}
    rows = {
        month: {
            "跑量": round(r["run_km"], 1),
            "課次": r["run_sessions"],
            "肌力次數": r["strength_count"],
            "負荷總和": r["training_load_sum"],
        }
        for month, r in months.items()
    }
    rows["累計"] = {
        "跑量": round(sum(r["run_km"] for r in months.values()), 1),
        "課次": sum(r["run_sessions"] for r in months.values()),
        "肌力次數": sum(r["strength_count"] for r in months.values()),
        "負荷總和": sum(r["training_load_sum"] for r in months.values()),
    }
    return {"key_re": r"(2026-\d\d|累計)", "rows": rows}


def _yearly_from_monthly(relpath, fields):
    totals = collections.defaultdict(lambda: collections.Counter())
    for row in load_json(relpath):
        for field in fields:
            totals[row["month"][:4]][field] += row.get(field, 0)
    return totals


@checker("coros_yearly", src="data/analysis/monthly_summary.json")
def _coros_yearly(_):
    fields = ("run_km", "run_sessions", "strength_count", "training_load_sum")
    totals = _yearly_from_monthly("data/analysis/monthly_summary.json", fields)
    rows = {
        year: {
            "跑量": round(t["run_km"], 1),
            "課次": t["run_sessions"],
            "肌力次數": t["strength_count"],
            "負荷總和": t["training_load_sum"],
        }
        for year, t in totals.items()
    }
    return {"key_re": r"(20\d\d)", "rows": rows}


@checker("garmin_yearly", src="data/garmin_analysis/monthly_summary.json")
def _garmin_yearly(_):
    fields = ("run_km", "run_sessions", "training_load_sum")
    totals = _yearly_from_monthly("data/garmin_analysis/monthly_summary.json", fields)
    rows = {
        year: {
            "跑量": round(t["run_km"], 1),
            "課次": t["run_sessions"],
            "負荷總和": round(t["training_load_sum"], 1),
        }
        for year, t in totals.items()
    }
    return {"key_re": r"(20\d\d)", "rows": rows}


@checker("feel_type_by_year", src="data/coros.db")
def _feel_type_by_year(_):
    rows = {}
    query = """
        SELECT strftime('%Y', datetime(start_time,'unixepoch','localtime')) AS y,
               COUNT(*), SUM(feel_type IS NOT NULL),
               SUM(feel_type <= 3), SUM(feel_type >= 4)
        FROM activities GROUP BY y
    """
    for year, total, filled, low, high in coros_db().execute(query):
        filled = filled or 0
        rows[year] = {
            "全部活動": total,
            "有填": filled,
            "填寫率": round(100 * filled / total, 1),
            "≤3分": low or 0,
            "≥4分": high or 0,
        }
    return {"key_re": r"(20\d\d)", "rows": rows}


@checker("coros_data_volume", src="data/coros.db", kind="scalars")
def _coros_data_volume(_):
    db = coros_db()
    count = lambda table: list(db.execute(f"SELECT COUNT(*) FROM {table}"))[0][0]
    weeks = len(load_json("data/analysis/weekly_summary.json"))
    months = len(load_json("data/analysis/monthly_summary.json"))
    return {
        "values": [
            ("活動", count("activities")),
            ("sessions", count("sessions")),
            ("生理紀錄", count("daily_metrics")),
            ("睡眠紀錄", count("sleep_records")),
            ("週", weeks),
            ("月", months),
            ("長跑", len(load_json("data/analysis/long_runs.json"))),
            ("強度課", len(load_json("data/analysis/quality_sessions.json"))),
            ("課表庫", count("workouts")),
            ("排定行事曆", count("scheduled_workouts")),
        ]
    }


@checker("coros_weekly_spread", src="data/analysis/weekly_summary.json", kind="scalars")
def _coros_weekly_spread(_):
    weeks = load_json("data/analysis/weekly_summary.json")
    values = sorted(w["run_km"] for w in weeks)
    average = mean(values)
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    variance = mean([(v - average) ** 2 for v in values])
    busiest = max(weeks, key=lambda w: w["run_km"])
    quietest = min((w for w in weeks if w["run_km"] > 0), key=lambda w: w["run_km"])
    return {
        "values": [
            ("週數", len(weeks)),
            ("平均", round(average, 1)),
            ("中位數", round(median, 1)),
            ("標準差", round(variance ** 0.5, 1)),
            ("最大單週", round(busiest["run_km"], 1)),
            ("最小非零週", round(quietest["run_km"], 1)),
        ]
    }


@checker("garmin_weekly_spread", src="data/garmin_analysis/weekly_summary.json", kind="scalars")
def _garmin_weekly_spread(_):
    weeks = load_json("data/garmin_analysis/weekly_summary.json")
    values = [w["run_km"] for w in weeks]
    return {
        "values": [
            ("週數", len(weeks)),
            ("平均", round(mean(values), 1)),
            ("最大單週", round(max(values), 1)),
        ]
    }


@checker("long_run_distance_spread", src="data/analysis/long_runs.json", kind="scalars")
def _long_run_distance_spread(_):
    runs = load_json("data/analysis/long_runs.json")

    def bucket(km):
        if km < 25:
            return "<25"
        if km < 30:
            return "25-30"
        if km < 35:
            return "30-35"
        if km < 42:
            return "35-42"
        return ">=42"

    counts = collections.Counter(bucket(r["distance_km"]) for r in runs)
    return {
        "values": [
            ("總筆數", len(runs)),
            ("<25km", counts["<25"]),
            ("25-30km", counts["25-30"]),
            ("30-35km", counts["30-35"]),
            ("35-42km", counts["35-42"]),
            ("≥42km", counts[">=42"]),
        ]
    }


@checker("long_run_2026_monthly_max", src="data/analysis/long_runs.json", kind="scalars")
def _long_run_2026_monthly_max(_):
    peaks = {}
    for run in load_json("data/analysis/long_runs.json"):
        month = str(run["date"])[:7]
        if month.startswith("2026"):
            peaks[month] = max(peaks.get(month, 0), run["distance_km"])
    return {"values": [(month, round(km, 1)) for month, km in sorted(peaks.items())]}


@checker("garmin_long_runs", src="data/garmin_analysis/long_runs.json", kind="scalars")
def _garmin_long_runs(_):
    return {
        "values": [
            ("長跑", len(load_json("data/garmin_analysis/long_runs.json"))),
            ("強度課", len(load_json("data/garmin_analysis/quality_sessions.json"))),
        ]
    }


HALF_MARATHON_KM = 21.0975

COROS_TAIPEI = {
    "2021": "2021-12-19",
    "2022": "2022-12-18",
    "2023": "2023-12-17",
    "2024": "2024-12-15",
    "2025": "2025-12-21",
}
GARMIN_TAIPEI = {
    "2017": "2017-12-17",
    "2018": "2018-12-09",
    "2019": "2019-12-15",
    "2020": "2020-12-20",
}


def _split_from_laps(laps, to_km, to_seconds, avg_hr):
    """Shared half-split maths. `to_km`/`to_seconds` absorb the per-device units."""
    distance = elapsed = 0.0
    first_half = None
    for lap in laps:
        lap_km = to_km(lap)
        lap_seconds = to_seconds(lap)
        if first_half is None and distance + lap_km >= HALF_MARATHON_KM:
            share = (HALF_MARATHON_KM - distance) / lap_km if lap_km else 0
            first_half = elapsed + lap_seconds * share
        distance += lap_km
        elapsed += lap_seconds
    if first_half is None:
        return None

    second_half = elapsed - first_half
    to_clock = lambda s: f"{int(s) // 3600}:{int(s) % 3600 // 60:02d}:{int(s) % 60:02d}"
    return {
        "手錶距離/時間": round(distance, 2),
        "前半": to_clock(first_half),
        "後半": to_clock(second_half),
        "後半慢": {"value": round((second_half - first_half) / 60, 1), "tol": 0.5},
        "前半配速": fmt_pace(first_half / HALF_MARATHON_KM),
        "後半配速": fmt_pace(second_half / (distance - HALF_MARATHON_KM)),
        "平均HR": avg_hr,
    }


def _coros_race_splits(day):
    found = list(
        coros_db().execute(
            """SELECT activity_id, detail_db_path, avg_hr FROM activities
               WHERE date(datetime(start_time,'unixepoch','localtime')) = ?
                 AND distance_meters > 41000""",
            (day,),
        )
    )
    if not found:
        return None
    activity_id, detail_path, avg_hr = found[0]
    detail = sqlite3.connect(os.path.join(ROOT, "data", detail_path))
    payload = list(detail.execute("SELECT detail_json FROM activity_detail WHERE activity_id=?", (activity_id,)))
    if not payload:
        return None
    laps = json.loads(payload[0][0])["lapList"][0]["lapItemList"]
    return _split_from_laps(
        laps,
        lambda lap: (lap.get("distance") or 0) / 100000.0,  # centimetres
        lambda lap: (lap.get("time") or 0) / 100.0,         # centiseconds
        avg_hr,
    )


def _garmin_race_splits(day):
    """Garmin-era races.

    Two traps, both real:
      - `start_time` holds a naive local time stamped as if it were UTC, so a
        06:30 start reads back as the previous day 22:30 under 'localtime'.
        Correct it with an explicit +8 hours rather than shifting the query date.
      - Lap data lives at `splits.lapDTOs` (metres / seconds), not at
        `lapList[0].lapItemList` (centimetres / centiseconds) like COROS.
      - `activities.distance_meters` is often the rounded 42.2 km rather than the
        GPS distance; the wiki quotes the lap-summed distance, so sum the laps.
    """
    found = list(
        sqlite3.connect(os.path.join(ROOT, "data", "garmin.db")).execute(
            """SELECT activity_id, detail_db_path, avg_hr FROM activities
               WHERE date(datetime(start_time,'unixepoch','localtime','+8 hours')) = ?
                 AND name LIKE '%台北馬拉松%' AND name NOT LIKE '%渣打%'
                 AND distance_meters > 40000""",
            (day,),
        )
    )
    if not found:
        return None
    activity_id, detail_path, avg_hr = found[0]
    detail = sqlite3.connect(os.path.join(ROOT, "data", detail_path))
    payload = list(detail.execute("SELECT detail_json FROM activity_detail WHERE activity_id=?", (activity_id,)))
    if not payload:
        return None
    laps = json.loads(payload[0][0])["splits"]["lapDTOs"]
    return _split_from_laps(
        laps,
        lambda lap: (lap.get("distance") or 0) / 1000.0,  # metres
        lambda lap: lap.get("duration") or 0,             # seconds
        avg_hr,
    )


@checker("taipei_race_splits", src="data/coros.db + data/garmin.db + 兩邊的 detail DB")
def _taipei_race_splits(_):
    rows = {}
    for year, day in GARMIN_TAIPEI.items():
        splits = _garmin_race_splits(day)
        if splits:
            rows[year] = splits
    for year, day in COROS_TAIPEI.items():
        splits = _coros_race_splits(day)
        if splits:
            rows[year] = splits
    return {"key_re": r"(20\d\d)", "rows": rows}


@checker("smile_trek_load", src="data/coros.db", kind="scalars")
def _smile_trek_load(_):
    """The 2026-07-20~24 hiking block.

    Guards the three things that were wrong until 2026-08-16: the day-level load
    on 07-20 (the table's 330 is the hike alone; the day was 510 because the
    prescribed indoor run happened too), the total ascent (written as 'near
    4800m', actually 5433m), and which day held the tired_rate peak (07-21, not
    07-20).
    """
    db = coros_db()
    window = ("2026-07-20", "2026-07-25")
    hike = list(
        db.execute(
            """SELECT round(SUM(distance_meters)/1000.0,1), SUM(elevation_gain)
               FROM activities WHERE sport_name='Walking' AND name NOT LIKE '%三貂嶺%'
                 AND datetime(start_time,'unixepoch','localtime') >= ?
                 AND datetime(start_time,'unixepoch','localtime') < ?""",
            window,
        )
    )[0]
    day_total = list(
        db.execute(
            """SELECT round(SUM(distance_meters)/1000.0,1), SUM(training_load) FROM activities
               WHERE date(datetime(start_time,'unixepoch','localtime'))='2026-07-20'"""
        )
    )[0]
    peak_tired = list(
        db.execute(
            "SELECT MAX(tired_rate) FROM daily_metrics WHERE date BETWEEN '20260712' AND '20260726'"
        )
    )[0][0]
    peak_rhr = list(
        db.execute("SELECT MAX(rhr) FROM daily_metrics WHERE date BETWEEN '20260712' AND '20260726'")
    )[0][0]
    return {
        "values": [
            ("健行距離", hike[0]),
            ("健行爬升", int(hike[1])),
            ("07-20 當日距離", day_total[0]),
            ("07-20 當日TL", day_total[1]),
            ("窗口 tired_rate 峰值", int(peak_tired)),
            ("窗口 RHR 峰值", peak_rhr),
        ]
    }


@checker("smile_trek_totals", src="data/coros.db", kind="scalars")
def _smile_trek_totals(_):
    """The trek headline numbers, which three separate pages restate.

    A checker may be anchored on as many pages as repeat its facts, and every
    anchor is checked independently — so this doubles as a guard against the
    wiki's most persistent failure mode: a correction landing on one copy of a
    fact and not the others. The 'near 4800m' ascent survived on this page for
    three weeks after being corrected elsewhere; so did '第1~14段中的大部分'
    after the user had confirmed all 14 were done.
    """
    full = _smile_trek_load(None)["values"]
    wanted = {"健行距離", "健行爬升", "07-20 當日距離"}
    return {"values": [(label, value) for label, value in full if label in wanted]}


@checker("coros_yearly_totals", src="data/analysis/monthly_summary.json", kind="scalars")
def _coros_yearly_totals(_):
    """The per-year COROS mileage, restated in prose on entities/runner.md.

    The table on load-management.md is guarded by `coros_yearly`; this exists so
    the prose copy cannot drift away from it independently.
    """
    totals = _yearly_from_monthly("data/analysis/monthly_summary.json", ("run_km",))
    return {"values": [(year, round(t["run_km"], 1)) for year, t in sorted(totals.items()) if year < "2026"]}


@checker("garmin_yearly_totals", src="data/garmin_analysis/monthly_summary.json", kind="scalars")
def _garmin_yearly_totals(_):
    """Same idea for the Garmin years. runner.md rounds to whole km, so match that.

    Use half-up rounding, not `round()`: Python rounds half to even, which turns
    2016's 2236.5 km into 2236 while the page (correctly, by the convention a
    human uses) says 2237. A checker that is right about the data and wrong
    about the convention just produces noise.
    """
    totals = _yearly_from_monthly("data/garmin_analysis/monthly_summary.json", ("run_km",))
    half_up = lambda km: int(km + 0.5)
    return {"values": [(year, half_up(t["run_km"])) for year, t in sorted(totals.items())]}


@checker("coros_db_counts", src="data/coros.db", kind="scalars")
def _coros_db_counts(_):
    """The five raw table counts, restated in sources.md as well as load-management."""
    db = coros_db()
    count = lambda table: list(db.execute(f"SELECT COUNT(*) FROM {table}"))[0][0]
    return {
        "values": [
            ("活動", count("activities")),
            ("生理", count("daily_metrics")),
            ("睡眠", count("sleep_records")),
            ("課表庫", count("workouts")),
            ("行事曆", count("scheduled_workouts")),
        ]
    }


@checker("long_run_2026_gap", src="data/analysis/long_runs.json", kind="scalars")
def _long_run_2026_gap(_):
    """The season's headline gap, restated on three pages.

    Only the long-run count and the ≥30km count are checked. The ≥35km figure is
    0, and a presence check for '0' would match almost any prose — so that half
    of the claim is deliberately unguarded rather than falsely reassuring.
    """
    runs = [r for r in load_json("data/analysis/long_runs.json") if str(r["date"]).startswith("2026")]
    return {
        "values": [
            ("2026 長跑次數", len(runs)),
            ("≥30km 次數", sum(1 for r in runs if r["distance_km"] >= 30)),
        ]
    }


@checker("health_bank_counts", src="raw/健康存摺醫療類_1150719.JSON", kind="scalars")
def _health_bank_counts(_):
    path = os.path.join(ROOT, "raw", "健康存摺醫療類_1150719.JSON")
    with open(path, encoding="utf-8-sig") as handle:  # the export carries a BOM
        records = json.load(handle)["myhealthbank"]["bdata"]
    return {
        "values": [
            ("西醫門診", len(records["r1"])),
            ("牙科", len(records["r3"])),
            ("預防接種", len(records["r6"])),
        ]
    }


@checker("marathon_race_count", src="doc/marathon-race-history.md", kind="scalars")
def _marathon_race_count(_):
    """The '97 場全馬' figure, counted from the source table rather than trusted."""
    with open(os.path.join(ROOT, "doc", "marathon-race-history.md"), encoding="utf-8") as handle:
        text = handle.read()
    numbered = {int(m) for m in re.findall(r"^\|\s*(\d{1,3})\s*\|", text, re.MULTILINE)}
    return {"values": [("全馬場次", max(numbered) if numbered else 0)]}


@checker("long_run_tired_rate", src="data/analysis/long_runs.json", kind="scalars")
def _long_run_tired_rate(_):
    runs = [r for r in load_json("data/analysis/long_runs.json") if r.get("tired_rate") is not None]
    good = [r["tired_rate"] for r in runs if r.get("feel_type") in (4, 5)]
    rest = [r["tired_rate"] for r in runs if r.get("feel_type") not in (4, 5)]
    return {
        "values": [
            ("感受好 tired_rate", round(mean(good), 1)),
            ("感受不好 tired_rate", round(mean(rest), 1)),
            ("n 感受好", len(good)),
            ("n 感受不好", len(rest)),
        ]
    }


# --------------------------------------------------------------------------
# wiki parsing
# --------------------------------------------------------------------------

ANCHOR = re.compile(r"<!--\s*derived(?P<scalars>-scalars)?:\s*(?P<name>[a-z0-9_]+)(?P<rest>[^>]*)-->")


def wiki_files():
    for folder, _, names in os.walk(WIKI):
        for name in sorted(names):
            if name.endswith(".md"):
                yield os.path.join(folder, name)


def find_anchors():
    """Yield (checker_name, is_scalars, path, line_index, lines).

    Fenced code blocks are skipped: WIKI.md documents the anchor syntax inside
    ``` fences, and those examples must not be mistaken for real anchors (the
    verifier caught exactly this on itself the first time it ran).
    """
    for path in wiki_files():
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        in_fence = False
        for index, line in enumerate(lines):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = ANCHOR.search(line)
            if match:
                yield match.group("name"), bool(match.group("scalars")), path, index, lines


def read_table(lines, start):
    """Return (headers, rows) for the first markdown table at or after `start`."""
    index = start
    while index < len(lines) and not lines[index].lstrip().startswith("|"):
        if lines[index].strip() and not lines[index].lstrip().startswith("<!--"):
            return None, None  # prose got in the way; anchor is misplaced
        index += 1
    if index >= len(lines):
        return None, None
    cells = lambda line: [c.strip() for c in line.strip().strip("|").split("|")]
    headers = cells(lines[index])
    index += 2  # skip the |---|---| separator
    rows = []
    while index < len(lines) and lines[index].lstrip().startswith("|"):
        rows.append(cells(lines[index]))
        index += 1
    return headers, rows


def read_paragraph(lines, start):
    index = start
    while index < len(lines) and not lines[index].strip():
        index += 1
    collected = []
    while index < len(lines) and lines[index].strip():
        collected.append(lines[index])
        index += 1
    return "\n".join(collected)


NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
CLOCK = re.compile(r"\d{1,2}:\d\d(?::\d\d)?")


def clock_seconds(text):
    parts = [float(p) for p in text.split(":")]
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def cell_matches(cell, expected):
    """Numeric comparison where possible so '32.5', '32.5 km' and '**32.5 km**' agree.

    `expected` may be:
      - a number                  compared numerically (ints exact, floats ±0.051)
      - 'm:ss' or 'h:mm:ss'       compared as elapsed time; a pace must land within
                                  2s, an elapsed split within 30s (half-marathon
                                  split boundaries are interpolated between laps,
                                  so recomputation legitimately differs by seconds)
      - {'value': x, 'tol': y}    explicit tolerance, for columns where the wiki's
                                  original derivation is close but not identical
    """
    tolerance = None
    if isinstance(expected, dict):
        tolerance = expected["tol"]
        expected = expected["value"]

    if isinstance(expected, str) and ":" in expected:
        found = CLOCK.search(cell)
        if found is None:
            return False
        limit = tolerance if tolerance is not None else (2 if expected.count(":") == 1 else 30)
        return abs(clock_seconds(found.group(0)) - clock_seconds(expected)) <= limit

    found = NUMBER.search(cell.replace(",", ""))
    if found is None:
        return False
    actual = float(found.group(0))
    if tolerance is not None:
        return abs(actual - float(expected)) <= tolerance
    if isinstance(expected, int):
        return abs(actual - expected) < 0.5
    return abs(actual - float(expected)) <= 0.051


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------


def _rel(path):
    """Repo-relative display path, falling back to the path as given.

    `os.path.relpath` raises ValueError on Windows when the two paths sit on
    different drives. That happens whenever the tests point a checker at a
    temp file (pytest's tmp_path is on C:, the repo is on S:), which made all
    seven negative-case tests error out on Windows — the exact tests that are
    supposed to prove this file catches real drift. The location string is
    cosmetic, so degrade instead of raising.
    """
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return str(path)


def check_table(spec, result, path, lines, index):
    problems = []
    where = f"{_rel(path)}:{index + 1}"
    headers, rows = read_table(lines, index + 1)
    if headers is None:
        return [f"{where} [{spec['name']}] 錨點下面找不到表格"]

    key_re = re.compile(result["key_re"])
    seen = {}
    for row in rows:
        match = key_re.search(row[0])
        if match:
            seen[match.group(1)] = row

    for key, expected_columns in sorted(result["rows"].items()):
        row = seen.get(key)
        if row is None:
            problems.append(f"{where} [{spec['name']}] 表格缺少 {key} 這一列（原始資料有）")
            continue
        for column, expected in expected_columns.items():
            if column not in headers:
                problems.append(f"{where} [{spec['name']}] 表格沒有「{column}」欄")
                continue
            position = headers.index(column)
            cell = row[position] if position < len(row) else ""
            if not cell_matches(cell, expected):
                problems.append(
                    f"{where} [{spec['name']}] {key} 的「{column}」：wiki 寫 {cell!r}，重算為 {expected}"
                )

    if not result.get("allow_extra_rows"):
        for key in sorted(set(seen) - set(result["rows"])):
            problems.append(f"{where} [{spec['name']}] 表格多出 {key} 這一列（原始資料沒有）")
    return problems


def check_scalars(spec, result, path, lines, index):
    where = f"{_rel(path)}:{index + 1}"
    text = read_paragraph(lines, index + 1)
    if not text:
        return [f"{where} [{spec['name']}] 錨點下面找不到內文"]
    present = set(NUMBER.findall(text.replace(",", "")))
    problems = []
    for label, value in result["values"]:
        rendered = f"{value:.1f}" if isinstance(value, float) else str(value)
        if rendered not in present:
            problems.append(
                f"{where} [{spec['name']}] 內文找不到「{label}」的重算值 {rendered}"
            )
    return problems


def run():
    problems = []
    used = set()
    for name, is_scalars, path, index, lines in find_anchors():
        spec = CHECKERS.get(name)
        if spec is None:
            problems.append(
                f"{_rel(path)}:{index + 1} 錨點 [{name}] 沒有對應的 checker"
            )
            continue
        used.add(name)
        wants_scalars = spec["kind"] == "scalars"
        if wants_scalars != is_scalars:
            wanted = "derived-scalars" if wants_scalars else "derived"
            problems.append(
                f"{_rel(path)}:{index + 1} 錨點 [{name}] 型別用錯，應為 {wanted}"
            )
            continue
        result = spec["fn"](ROOT)
        handler = check_scalars if wants_scalars else check_table
        problems.extend(handler(spec, result, path, lines, index))

    for name in sorted(set(CHECKERS) - used):
        problems.append(f"checker [{name}] 沒有任何 wiki 頁面在用（錨點被刪掉了？）")
    return problems, used


def _force_utf8_output():
    """Make stdout/stderr UTF-8 so the report survives a non-UTF-8 console.

    Windows consoles default to cp950 here, which cannot encode `✓` or `⚠️` —
    every checker would run and pass, then the final print would raise
    UnicodeEncodeError and the process would exit non-zero. `githooks/post-commit`
    reads that exit code, so a clean wiki got reported as failing drift.
    Reconfiguring is enough; callers no longer need PYTHONIOENCODING=utf-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - py<3.7 or replaced stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - detached/odd stream
            pass


def main(argv=None):
    _force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--list", action="store_true", help="列出已註冊的 checker 就結束")
    parser.add_argument("--quiet", action="store_true", help="沒問題時不輸出")
    args = parser.parse_args(argv)

    if args.list:
        for name, spec in sorted(CHECKERS.items()):
            print(f"{name:26} {spec['kind']:8} {spec['src']}")
        return 0

    problems, used = run()
    if problems:
        print(f"⚠️  wiki 數字對帳發現 {len(problems)} 個問題：\n")
        for problem in problems:
            print(f"  - {problem}")
        print("\n重算依據是 data/analysis/*.json 與 data/coros.db。若原始資料才是對的，")
        print("請改 wiki；若是同步造成的正常變動，一樣要把 wiki 更新到最新值。")
        return 1
    if not args.quiet:
        print(f"✓ wiki 數字對帳通過（{len(used)} 個 checker，全部與原始資料一致）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
