"""
Network side of create_garmin_workouts.py: build the typed Garmin workout
objects, upload them, verify what Garmin stored, then schedule.

Separate module so create_garmin_workouts.py's --dry-run path never imports
garminconnect/pydantic (mirrors why coros_runner.py is split out from
create_workouts.py).
"""

import sys

from garmin_auth import get_client
from garmin_workout_plan import (
    GARMIN_PACE_ZONE_TARGET_TYPE_ID,
    expected_duration_seconds,
    speeds_from_pace_range,
    verify,
)

try:
    from garminconnect.workout import (
        ConditionType,
        ExecutableStep,
        RunningWorkout,
        StepType,
        WorkoutSegment,
    )
except ImportError:
    sys.exit(
        "pydantic is required for typed Garmin workouts. Install the "
        "`garminconnect[workout]` extra, e.g.:\n"
        "  uv run create_garmin_workouts.py --plan <file>\n"
        "or `pip install garminconnect[workout]`."
    )


# garminconnect.workout's create_warmup_step()/create_cooldown_step() are
# hardcoded to a specific end condition (TIME), but this project's training
# plans may have either distance-based or time-based segments. So every step
# kind is built directly from ExecutableStep below, with dynamic end condition
# selection based on whether step["distance_m"] is None (see _build_step).
_STEP_TYPE_BY_KIND = {
    "warmup": {"stepTypeId": StepType.WARMUP, "stepTypeKey": "warmup", "displayOrder": 1},
    "training": {"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
    "cooldown": {"stepTypeId": StepType.COOLDOWN, "stepTypeKey": "cooldown", "displayOrder": 2},
}

_DISTANCE_END_CONDITION = {
    "conditionTypeId": ConditionType.DISTANCE,
    "conditionTypeKey": "distance",
    "displayOrder": 3,
    "displayable": True,
}

_TIME_END_CONDITION = {
    "conditionTypeId": ConditionType.TIME,
    "conditionTypeKey": "time",
    "displayOrder": 3,
    "displayable": True,
}

# Pre-existing bug fix (found while working Task 8, not part of its scope):
# `garminconnect.workout.TargetType` (the package's own convenience enum,
# as published on PyPI up to at least 0.3.2) has no PACE_ZONE member — it
# only defines NO_TARGET/POWER/CADENCE/HEART_RATE/SPEED/OPEN. Referencing
# `TargetType.PACE_ZONE` raised AttributeError on module import with any
# published garminconnect[workout] install, so this module could never
# actually be imported outside a mocked test. Use the same hardcoded ID
# garmin_workout_plan.py already uses (confirmed against a real account,
# see GARMIN_PACE_ZONE_TARGET_TYPE_ID's definition) instead of the
# nonexistent enum member.
_PACE_ZONE_TARGET_TYPE = {
    "workoutTargetTypeId": GARMIN_PACE_ZONE_TARGET_TYPE_ID,
    "workoutTargetTypeKey": "pace.zone",
    "displayOrder": 1,
}


def _build_step(step, step_order):
    """One garmin_workout_plan.build_steps() dict -> one Garmin ExecutableStep.

    See garmin_workout_plan.speeds_from_pace_range() for the pace->speed
    conversion and its confirmation status.
    """
    min_speed, max_speed = speeds_from_pace_range(step["pace_fast_s"], step["pace_slow_s"])
    if step["distance_m"] is not None:
        end_condition = _DISTANCE_END_CONDITION
        end_condition_value = float(step["distance_m"])
    else:
        end_condition = _TIME_END_CONDITION
        end_condition_value = float(step["duration_s"])
    return ExecutableStep(
        stepOrder=step_order,
        stepType=_STEP_TYPE_BY_KIND[step["kind"]],
        endCondition=end_condition,
        endConditionValue=end_condition_value,
        targetType=_PACE_ZONE_TARGET_TYPE,
        targetValueOne=min_speed,
        targetValueTwo=max_speed,
    )


def _build_repeat_group(item, step_order):
    """One {"kind": "repeat", ...} dict (from garmin_workout_plan.build_steps())
    -> one Garmin RepeatGroup, using the package's own create_repeat_group().
    """
    from garminconnect.workout import create_repeat_group

    child_steps = [
        _build_step(child, step_order + 1 + i)
        for i, child in enumerate(item["steps"])
    ]
    group = create_repeat_group(item["repeat_count"], child_steps, step_order)
    # `WorkoutSegment.workoutSteps` 宣告成 `list[ExecutableStep | RepeatGroup]`。
    # pydantic 2 正確保留 RepeatGroup；**pydantic 1 沒有那種 union 支援，會把它
    # 轉型成 ExecutableStep 並丟掉 numberOfIterations**——上傳一個沒有重複次數的
    # 課表，而且完全不報錯（2026-09-13 查出，當時看起來像 garmin_runner 壞了，
    # 其實是測試環境少了 garminconnect[workout] 帶來的 pydantic>=2）。
    if getattr(group, "numberOfIterations", None) is None:
        raise RuntimeError(
            "重複組被轉型成不帶 numberOfIterations 的物件，通常是 pydantic 版本太舊"
            "（需要 >=2，由 garminconnect[workout] extra 提供）。硬送上去會得到一份"
            "沒有重複次數的課表，所以在這裡擋下來。"
        )
    return group


def build_workout(name, steps):
    """Garmin step dicts (from garmin_workout_plan.build_steps()) -> RunningWorkout."""
    workout_steps = []
    order = 1
    for step in steps:
        if step["kind"] == "repeat":
            group = _build_repeat_group(step, order)
            workout_steps.append(group)
            order += 1 + len(step["steps"])
        else:
            workout_steps.append(_build_step(step, order))
            order += 1
    return RunningWorkout(
        workoutName=name,
        estimatedDurationInSecs=round(expected_duration_seconds(steps)),
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
                workoutSteps=workout_steps,
            )
        ],
    )


def _iso_date(day):
    """'20260809' -> '2026-08-09' (schedule_workout's required format)."""
    return f"{day[0:4]}-{day[4:6]}-{day[6:8]}"


def _process(client, entry, steps):
    try:
        workout = build_workout(entry.name, steps)
        uploaded = client.upload_running_workout(workout)
        workout_id = uploaded.get("workoutId")
    except Exception as error:  # noqa: BLE001 - report and don't abort the run
        print(f"  ✗ 建立失敗：{error}")
        return False
    if not workout_id:
        print(f"  ✗ 建立失敗：回應中找不到 workoutId：{uploaded}")
        return False
    print(f"  建立課表 {workout_id}")

    try:
        readback = client.get_workout_by_id(workout_id)
        problems = verify(readback, steps)
    except Exception as error:  # noqa: BLE001 - report and clean up, don't abort the run
        print(f"  ✗ 讀回驗證失敗：{error}")
        try:
            client.delete_workout(workout_id)
            print(f"  已刪除無法驗證的課表 {workout_id}")
        except Exception as cleanup_error:  # noqa: BLE001
            print(
                f"  ⚠ 清理無法驗證的課表 {workout_id} 也失敗：{cleanup_error} —— "
                "請手動刪除該課表（不影響行事曆，該課表未被排程）"
            )
        return False

    if problems:
        print("  ✗ 驗證未通過：")
        for problem in problems:
            print(f"      {problem}")
        print(f"  刪除未通過驗證的課表 {workout_id}，行事曆未變動")
        try:
            client.delete_workout(workout_id)
        except Exception as cleanup_error:  # noqa: BLE001
            print(
                f"  ⚠ 清理未通過驗證的課表 {workout_id} 也失敗：{cleanup_error} —— "
                "請手動刪除該課表（不影響行事曆，該課表未被排程）"
            )
        return False

    print("  ✓ 驗證通過（步驟數、距離、配速、預估時長）")

    try:
        client.schedule_workout(workout_id, _iso_date(entry.day))
    except Exception as error:  # noqa: BLE001 - report and clean up, don't abort the run
        print(f"  ✗ 排程失敗：{error}")
        try:
            client.delete_workout(workout_id)
            print(f"  已刪除未能排入行事曆的課表 {workout_id}")
        except Exception as cleanup_error:  # noqa: BLE001
            print(
                f"  ⚠ 清理未排入的課表 {workout_id} 也失敗：{cleanup_error} —— "
                "請手動刪除該課表（不影響行事曆，該課表未被排程）"
            )
        return False

    print(f"  排入 {entry.day}")
    return True


def run(plans):
    try:
        client = get_client()
        ok = True
        for entry, steps in plans:
            print(f"{entry.day}  {entry.name}")
            ok = _process(client, entry, steps) and ok
            print()
        return 0 if ok else 1
    except Exception as error:  # noqa: BLE001 - surface the cause plus a hint
        print(f"Garmin 操作失敗：{error}", file=sys.stderr)
        print("先確認 ~/.garminconnect/ 或 .env 裡的 GARMIN_EMAIL/GARMIN_PASSWORD", file=sys.stderr)
        return 2
