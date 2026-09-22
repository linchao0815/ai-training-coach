#!/usr/bin/env python3
# /// script
# dependencies = ["garminconnect"]
# ///
"""
Create (and schedule) recurring Garmin strength workouts.

Unlike create_garmin_workouts.py, this is NOT markdown-driven: a strength
routine typically doesn't change week to week the way a running plan does,
so there's no weekly plan doc to parse from. Exercise data is hardcoded in
`build_workouts()` below -- **replace the example workout with your own**.
To find the right `category`/`exerciseName` pair for a movement, either
check https://github.com/n1t3k/garmin-strength-api's exercise catalog, or
build a throwaway workout in the Garmin Connect app and read it back with
`client.get_workout_by_id()` to see exactly what Garmin stored.

    python create_garmin_strength_workouts.py --week-start 2026-09-21 --dry-run
    uv run create_garmin_strength_workouts.py --week-start 2026-09-21

Garmin has no strength-workout typed model in `garminconnect[workout]` (no
exercise/sets/reps schema at all -- confirmed by reading the package
source), so this path only needs the plain `garminconnect` package, not
the `[workout]` extra pydantic models create_garmin_workouts.py needs.
See garmin_strength_plan.py's module docstring for the schema notes and
gotchas (an unrecognised `exerciseName` is silently blanked; an invalid
`category` gets the whole upload rejected).

No dedup (see garmin_strength_runner.run()'s docstring): re-running this
for a week already scheduled creates duplicate calendar entries. Check
the Garmin calendar by hand before re-running for a week you already did.
"""

import argparse
import sys
from datetime import date, timedelta

from garmin_strength_plan import Circuit, Exercise, build_steps


def _force_utf8_output():
    """See create_garmin_workouts.py's identical helper for the incident
    this guards against (mid-run UnicodeEncodeError on a non-UTF-8 console
    leaving an orphaned, unscheduled workout)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover
            pass


def _dates_for(week_start, weekdays):
    """`week_start`: a Monday, as date.fromisoformat(). `weekdays`: 0=Mon..6=Sun."""
    return [(week_start + timedelta(days=offset)).isoformat() for offset in weekdays]


def build_workouts(week_start):
    """Returns [(name, items, dates), ...] -- see garmin_strength_runner.run().

    **This is example data — replace it with your own routine.** The shape
    below demonstrates both supported patterns: a plain list of individual
    exercises (each becomes its own single-set RepeatGroupDTO), and a
    `Circuit` (one movement sequence repeated N times as a round).
    """
    mon_thu = _dates_for(week_start, [0, 3])

    return [
        (
            "Example strength session",
            [
                Exercise("SQUAT", "GOBLET_SQUAT", "reps", 10, rest_s=45, weight_kg=8),
                Exercise("PLANK", "PLANK", "time", 45, rest_s=30),
                Circuit(3, [
                    Exercise("LUNGE", "FORWARD_LUNGE", "reps", 12, rest_s=30),
                    Exercise("HIP_RAISE", "HIP_RAISE", "reps", 15, rest_s=30),
                ]),
            ],
            mon_thu,
        ),
    ]


def describe(name, items, dates):
    lines = [name, f"  scheduled: {', '.join(dates)}"]
    for step in build_steps(items):
        children = step["workoutSteps"]
        iterations = step["numberOfIterations"]
        prefix = f"  x{iterations} " if iterations > 1 else "  "
        for child in children:
            if child["stepType"]["stepTypeKey"] != "interval":
                continue
            target = child["endCondition"]["conditionTypeKey"]
            unit = "s" if target == "time" else " reps"
            weight = f" @{child['weightValue']:.0f}kg" if child.get("weightValue") else ""
            lines.append(
                f"{prefix}{child['category']}/{child['exerciseName']} "
                f"{child['endConditionValue']:.0f}{unit}{weight}"
            )
    return "\n".join(lines)


def main():
    _force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--week-start", required=True,
                        help="Monday of the week to schedule, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print the parsed plan, don't connect or create anything")
    args = parser.parse_args()

    week_start = date.fromisoformat(args.week_start)
    if week_start.weekday() != 0:
        sys.exit(f"--week-start must be a Monday; {args.week_start} is weekday {week_start.weekday() + 1}")

    workouts = build_workouts(week_start)
    for name, items, dates in workouts:
        print(describe(name, items, dates))
        print()

    if args.dry_run:
        print("--dry-run: not connecting, nothing created.")
        return 0

    from garmin_strength_runner import run  # imported late so --dry-run needs no garminconnect

    return run(workouts)


if __name__ == "__main__":
    sys.exit(main())
