"""
Pure helpers for turning parsed training-plan segments into Garmin Connect
workout step descriptions, and for verify()-ing what Garmin actually stored
afterwards (step count, distance, pace conversion, estimated duration).

Standard library only, and deliberately no `garminconnect`/`pydantic`
import: this module must be importable without the `garminconnect[workout]`
extra so tests run without installing it. All typed-model construction and
network calls live in garmin_runner.py.

Segments come from `coros_workout_plan.parse_plan()` — the same
`<!-- coros: day=... name=... -->` anchored table COROS uses. The table
content (name/distance/pace) is platform-agnostic, so this module reuses
that parser rather than inventing a second markdown syntax.
"""

from coros_workout_plan import RepeatGroup

# Mirrors garminconnect.workout.TargetType.PACE_ZONE. Hardcoded here (not
# imported) because this module deliberately has no garminconnect
# dependency — see module docstring.
GARMIN_PACE_ZONE_TARGET_TYPE_ID = 6


def _format_pace(fast_s, slow_s):
    return f"{fast_s // 60}:{fast_s % 60:02d}-{slow_s // 60}:{slow_s % 60:02d}/km"


def build_steps(segments):
    """Segments -> Garmin step descriptions (plain dicts, no garminconnect types yet)."""
    steps = []
    for segment in segments:
        if isinstance(segment, RepeatGroup):
            steps.append({
                "kind": "repeat",
                "repeat_count": segment.repeat_count,
                "steps": build_steps(segment.steps),
            })
            continue
        steps.append({
            "kind": segment.kind,
            "name": f"{segment.name} ({_format_pace(segment.pace_fast_s, segment.pace_slow_s)})",
            "distance_m": segment.distance_m,
            "duration_s": segment.duration_s,
            "pace_fast_s": segment.pace_fast_s,
            "pace_slow_s": segment.pace_slow_s,
        })
    return steps


def expected_duration_seconds(steps):
    """Duration implied by distance x mid-range pace (or the duration itself), in seconds."""
    total = 0.0
    for step in steps:
        if step["kind"] == "repeat":
            total += step["repeat_count"] * expected_duration_seconds(step["steps"])
            continue
        mid_pace = (step["pace_fast_s"] + step["pace_slow_s"]) / 2.0
        if step["distance_m"] is not None:
            km = step["distance_m"] / 1000.0
            total += km * mid_pace
        else:
            total += step["duration_s"]
    return total


def speeds_from_pace_range(pace_fast_s, pace_slow_s):
    """(340, 355) sec/km -> (min_speed_m_s, max_speed_m_s).

    Garmin's pace-zone target stores a *speed* range in metres/second, not
    a pace range in seconds/km — the slower pace (larger seconds/km, here
    `pace_slow_s`) is the lower bound of the speed range, and the faster
    pace (`pace_fast_s`) is the upper bound.

    Confirmed against a real Garmin Connect account on 2026-09-01: uploaded
    a 1km step at pace_fast_s=300 (5:00/km), pace_slow_s=310 (5:10/km) and
    read back targetValueOne=3.2258065 (-> 1000/3.2258065 = 310.00 sec/km,
    the slow pace) and targetValueTwo=3.3333333 (-> 300.00 sec/km, the fast
    pace) — this direction round-trips correctly as originally guessed, no
    change needed. The upload response's id field is indeed named
    "workoutId", and the readback's duration field is indeed named
    "estimatedDurationInSecs". See .temp/claude/garmin_pace_probe.py.
    """
    return 1000.0 / pace_slow_s, 1000.0 / pace_fast_s


def pace_range_from_speeds(min_speed_m_s, max_speed_m_s):
    """Inverse of speeds_from_pace_range(), rounded to whole seconds/km."""
    return round(1000.0 / max_speed_m_s), round(1000.0 / min_speed_m_s)


# estimatedDurationInSecs 與自算值的容許誤差比例。
ESTIMATED_TIME_TOLERANCE = 0.2
# endConditionValue（公尺）浮點序列化的容許誤差。
DISTANCE_TOLERANCE_M = 1.0
# 配速換算回秒/公里後的容許誤差（浮點速度往返可能有 1 秒內的捨入差）。
PACE_TOLERANCE_S = 1


# Repeat-group readback shape confirmed against a real Garmin Connect
# account on 2026-09-02: a 2x{15s interval, 10s recovery} repeat group read
# back as `type == "RepeatGroupDTO"`, `numberOfIterations == 2`, with a
# nested `workoutSteps` array holding exactly 2 children (one copy of each
# step, NOT physically repeated -- `numberOfIterations` is a player-side
# repeat instruction, matching the COROS-side counterpart's `sets` finding).
# Each child came back as `type == "ExecutableStepDTO"` with the expected
# `endConditionValue` (15.0 / 10.0 seconds) and, notably, its pace-zone
# `targetType` (`workoutTargetTypeId == 6`) and `targetValueOne`/
# `targetValueTwo` applied correctly to steps NESTED inside the repeat
# group -- no special-casing needed for children vs top-level steps.
# stepOrder sequencing also matched _build_repeat_group()/build_workout()'s
# accumulator exactly (warmup=1, repeat container=2, child1=3, child2=4).
# See .temp/claude/garmin_repeat_probe.py.
def _flatten_steps(workout):
    steps = []
    for segment in (workout.get("workoutSegments") or []):
        for step in (segment.get("workoutSteps") or []):
            if not isinstance(step, dict):
                continue
            if step.get("type") == "RepeatGroupDTO":
                iterations = step.get("numberOfIterations") or 0
                inner = [s for s in (step.get("workoutSteps") or []) if isinstance(s, dict)]
                for _ in range(iterations):
                    steps.extend(inner)
            else:
                steps.append(step)
    return steps


def _flatten_expected_steps(expected_steps):
    """Expand `{"kind": "repeat", ...}` dicts so they line up positionally
    against `_flatten_steps()`'s already-expanded, un-repeated output."""
    flat = []
    for step in expected_steps:
        if step["kind"] == "repeat":
            flat.extend(step["steps"] * step["repeat_count"])
        else:
            flat.append(step)
    return flat


def verify(workout, expected_steps):
    """Check a workout read back from Garmin Connect. Empty list means it is safe to schedule."""
    problems = []
    actual_steps = _flatten_steps(workout)
    expected_steps = _flatten_expected_steps(expected_steps)

    if len(actual_steps) != len(expected_steps):
        problems.append(f"步驟數不符：預期 {len(expected_steps)}，實際 {len(actual_steps)}")

    actual_pace_pairs = set()
    for position, (actual, expected) in enumerate(zip(actual_steps, expected_steps), 1):
        label = f"第 {position} 步（{expected['name']}）"

        # `endConditionValue` is confirmed for distance steps (metres, see
        # DISTANCE_TOLERANCE_M docstring history). For time-based steps it
        # also carries the duration under the same field, in seconds — this
        # IS confirmed against a real Garmin Connect account (2026-09-02,
        # see the repeat-group readback docstring above `_flatten_steps()`:
        # endConditionValue 15.0 / 10.0 seconds for the two time-based steps).
        actual_distance = actual.get("endConditionValue")
        if expected["distance_m"] is not None:
            if actual_distance is None or abs(actual_distance - expected["distance_m"]) > DISTANCE_TOLERANCE_M:
                problems.append(f"{label} 距離不符：預期 {expected['distance_m']} 公尺，實際 {actual_distance}")
        else:
            if actual_distance is None or abs(actual_distance - expected["duration_s"]) > DISTANCE_TOLERANCE_M:
                problems.append(f"{label} 時長不符：預期 {expected['duration_s']} 秒，實際 {actual_distance}")

        target_type = actual.get("targetType") or {}
        if target_type.get("workoutTargetTypeId") != GARMIN_PACE_ZONE_TARGET_TYPE_ID:
            problems.append(
                f"{label} targetType 不是配速區間（workoutTargetTypeId="
                f"{target_type.get('workoutTargetTypeId')}）"
            )
            continue

        v1, v2 = actual.get("targetValueOne"), actual.get("targetValueTwo")
        if v1 is None or v2 is None:
            problems.append(f"{label} 缺少 targetValueOne/targetValueTwo")
            continue

        actual_fast, actual_slow = pace_range_from_speeds(v1, v2)
        actual_pace_pairs.add((actual_fast, actual_slow))
        if (abs(actual_fast - expected["pace_fast_s"]) > PACE_TOLERANCE_S
                or abs(actual_slow - expected["pace_slow_s"]) > PACE_TOLERANCE_S):
            problems.append(
                f"{label} 配速不符：預期 {_format_pace(expected['pace_fast_s'], expected['pace_slow_s'])}，"
                f"實際換算為 {_format_pace(actual_fast, actual_slow)}"
            )

    expected_pace_pairs = {(s["pace_fast_s"], s["pace_slow_s"]) for s in expected_steps}
    if len(expected_pace_pairs) > 1 and len(actual_pace_pairs) == 1:
        problems.append(
            "所有步驟配速換算後相同，但計畫指定了不同配速 —— "
            "可能是 targetValueOne/targetValueTwo 寫錯或塌成同一值"
        )

    estimated = workout.get("estimatedDurationInSecs")
    calculated = expected_duration_seconds(expected_steps)
    if estimated is None:
        problems.append("estimatedDurationInSecs 缺值")
    elif calculated and abs(estimated - calculated) > calculated * ESTIMATED_TIME_TOLERANCE:
        problems.append(
            f"estimatedDurationInSecs 不合理：{estimated}，"
            f"自算約 {calculated:.0f} 秒（差距超過 {ESTIMATED_TIME_TOLERANCE:.0%}）"
        )

    return problems
