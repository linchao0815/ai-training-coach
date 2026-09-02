#!/usr/bin/env python3
# /// script
# dependencies = ["garminconnect[workout]"]
# ///
"""
Create (and schedule) Garmin Connect run workouts from a training-plan
markdown doc — mirrors create_workouts.py's COROS flow, sharing the same
`<!-- coros: day=... name=... -->` anchored table. See
docs/superpowers/specs/2026-09-01-garmin-workout-sync-design.md.

    python create_garmin_workouts.py --plan doc/2026-W35-training-plan.md --dry-run
    uv run create_garmin_workouts.py --plan doc/2026-W35-training-plan.md

`--dry-run` only needs the standard library (no garminconnect/pydantic);
a real run needs `garminconnect[workout]`, installed automatically via
`uv run`'s PEP 723 inline dependency above.
"""

import argparse
import sys

from coros_workout_plan import parse_plan
from garmin_workout_plan import build_steps, expected_duration_seconds


def _force_utf8_output():
    """Make stdout/stderr UTF-8 so a print('✓...')/print('⚠...') survives a
    non-UTF-8 console (Windows defaults to cp950 here).

    Confirmed root cause of two real incidents during this feature's final
    end-to-end verification: both this CLI and create_workouts.py crashed
    with UnicodeEncodeError on such a print AFTER a workout was already
    created on the real account but BEFORE it was scheduled — leaving an
    orphaned, unscheduled workout that had to be found and deleted by hand.
    Mirrors verify_wiki_numbers.py's `_force_utf8_output()`.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - py<3.7 or replaced stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - detached/odd stream
            pass


def describe(entry, steps):
    """Human-readable summary printed for both dry-run and live runs."""
    def flat(step_list):
        result = []
        for step in step_list:
            if step["kind"] == "repeat":
                result.extend(flat(step["steps"]) * step["repeat_count"])
            else:
                result.append(step)
        return result

    flat_steps = flat(steps)
    total_km = sum(s["distance_m"] for s in flat_steps if s["distance_m"] is not None) / 1000
    duration = expected_duration_seconds(steps)
    lines = [
        f"{entry.day}  {entry.name}",
        f"  合計 {total_km:.1f}km（僅計距離型步驟，重複組已展開計入時長），"
        f"自算時長 {duration / 60:.0f} 分鐘",
    ]
    for position, step in enumerate(flat_steps, 1):
        target_desc = (
            f"{step['distance_m'] / 1000:>5.1f}km"
            if step["distance_m"] is not None
            else f"{step['duration_s']:>5.0f}秒"
        )
        lines.append(
            f"  {position}. [{step['kind']:<8}] {target_desc}  "
            f"{step['pace_fast_s'] // 60}:{step['pace_fast_s'] % 60:02d}-"
            f"{step['pace_slow_s'] // 60}:{step['pace_slow_s'] % 60:02d}/km"
        )
    return "\n".join(lines)


def main():
    _force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--plan", required=True,
                        help="訓練計畫 markdown 檔，需含 <!-- coros: ... --> 錨點")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印解析結果與步驟，不連線、不建立任何東西")
    args = parser.parse_args()

    with open(args.plan, encoding="utf-8") as handle:
        entries = parse_plan(handle.read())

    print(f"從 {args.plan} 解析出 {len(entries)} 堂課表\n")
    plans = [(entry, build_steps(entry.segments)) for entry in entries]
    for entry, steps in plans:
        print(describe(entry, steps))
        print()

    if args.dry_run:
        print("--dry-run：未連線，未建立任何東西。")
        return 0

    from garmin_runner import run  # imported late so --dry-run needs no garminconnect/pydantic

    return run(plans)


if __name__ == "__main__":
    sys.exit(main())
