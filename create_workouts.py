"""
Create (and schedule) COROS run workouts from a training-plan markdown doc.

Why this exists instead of hand-composing coros-training-mcp calls: that tool's
`pace` parameter silently collapses every step to the `Pace` preset defaults,
and coros_api's two distance display-unit fields don't share a safe default —
target_display_unit defaults to 3 (miles), intensity_display_unit to 0 — so
either one left unset can end up wrong. Both bugs shipped to the athlete's
watch on 2026-08-02. This script pins the units, then reads the workout back
and refuses to schedule it unless it verifies.

See docs/superpowers/specs/2026-08-02-coros-workout-creation-design.md

    python create_workouts.py --plan doc/2026-W32-training-plan.md
    python create_workouts.py --plan doc/2026-W32-training-plan.md --dry-run
"""

import argparse
import os
import sys

from coros_workout_plan import (
    build_steps,
    expected_duration_seconds,
    parse_plan,
)


def _force_utf8_output():
    """Make stdout/stderr UTF-8 so a print('✓...')/print('⚠...') survives a
    non-UTF-8 console (Windows defaults to cp950 here).

    Confirmed root cause of two real incidents during this feature's final
    end-to-end verification: both this CLI and create_garmin_workouts.py
    crashed with UnicodeEncodeError on such a print AFTER a workout was
    already created on the real account but BEFORE it was scheduled —
    leaving an orphaned, unscheduled workout that had to be found and
    deleted by hand. Mirrors verify_wiki_numbers.py's `_force_utf8_output()`.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - py<3.7 or replaced stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - detached/odd stream
            pass


def _relaunch_in_tool_venv():
    """Re-exec under the coros-training-mcp venv's Python if coros_api isn't importable."""
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
        f"tool venv to relaunch under. Try:\n  \"{candidate}\" {' '.join(sys.argv)}"
    )


def describe(entry, steps):
    """Human-readable summary printed for both dry-run and live runs.

    `steps` (build_steps()'s output) may contain repeat-group entries
    (`{"kind": "repeat", "repeat": N, "steps": [...]}` — see
    coros_workout_plan.build_steps()'s docstring for why that shape, not the
    is_group/sets/group_id wire shape, is what this function's own input
    actually looks like). `flat()` below expands those into a flat list purely
    for this human-readable printout; it does not affect
    expected_duration_seconds(steps) below, which still takes the original
    nested `steps` and applies the repeat multiplier itself.
    """
    def flat(step_list):
        result = []
        for step in step_list:
            if step["kind"] == "repeat":
                result.extend(flat(step["steps"]) * step["repeat"])
            else:
                result.append(step)
        return result

    flat_steps = flat(steps)
    total_km = sum(
        s["target_distance_meters"] for s in flat_steps if s["target_type"] == "distance"
    ) / 1000
    duration = expected_duration_seconds(steps)
    lines = [
        f"{entry.day}  {entry.name}",
        f"  合計 {total_km:.1f}km（僅計距離型步驟，重複組已展開計入時長），"
        f"自算時長 {duration / 60:.0f} 分鐘",
    ]
    for position, step in enumerate(flat_steps, 1):
        fast, slow = step["intensity_value"], step["intensity_value_extend"]
        if step["target_type"] == "distance":
            target_desc = f"{step['target_distance_meters'] / 1000:>5.1f}km"
        else:
            target_desc = f"{step['target_duration_seconds']:>5.0f}秒"
        lines.append(
            f"  {position}. [{step['kind']:<8}] {target_desc}  "
            f"{fast // 60}:{fast % 60:02d}-{slow // 60}:{slow % 60:02d}/km  "
            f"(target_display_unit={step.get('target_display_unit')}, "
            f"intensity_display_unit={step['intensity_display_unit']})"
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
        print("--dry-run：未連線，未建立任何課表。")
        return 0

    from coros_runner import run  # imported late so --dry-run needs no venv

    return run(plans)


if __name__ == "__main__":
    sys.exit(main())
