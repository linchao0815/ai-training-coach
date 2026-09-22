"""
Network side of create_garmin_strength_workouts.py: upload the raw payload
built by garmin_strength_plan.py, verify it, then schedule it onto each
requested date.

Separate module so --dry-run never needs `garminconnect` installed
(mirrors why garmin_runner.py is split out from create_garmin_workouts.py).
"""

import sys

from garmin_auth import get_client
from garmin_strength_plan import build_workout, verify


def _process(client, name, items, dates):
    try:
        payload = build_workout(name, items)
        uploaded = client.upload_workout(payload)
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
        problems, warnings = verify(readback, items)
    except Exception as error:  # noqa: BLE001 - report and clean up, don't abort the run
        print(f"  ✗ 讀回驗證失敗：{error}")
        _cleanup(client, workout_id)
        return False

    for warning in warnings:
        print(f"  ⚠ {warning}")

    if problems:
        print("  ✗ 驗證未通過：")
        for problem in problems:
            print(f"      {problem}")
        print(f"  刪除未通過驗證的課表 {workout_id}，行事曆未變動")
        _cleanup(client, workout_id)
        return False

    print("  ✓ 驗證通過（動作/次數-或-秒數/重量/休息秒數）")

    ok = True
    for date in dates:
        try:
            client.schedule_workout(workout_id, date)
            print(f"  排入 {date}")
        except Exception as error:  # noqa: BLE001 - report and keep trying the other dates
            print(f"  ✗ 排入 {date} 失敗：{error}")
            ok = False
    return ok


def _cleanup(client, workout_id):
    try:
        client.delete_workout(workout_id)
        print(f"  已刪除課表 {workout_id}")
    except Exception as cleanup_error:  # noqa: BLE001
        print(
            f"  ⚠ 清理課表 {workout_id} 也失敗：{cleanup_error} —— "
            "請手動刪除該課表（未被排程，不影響行事曆）"
        )


def run(workouts):
    """`workouts`: list of (name, items, dates). See garmin_strength_plan.py
    for `items`; `dates` are 'YYYY-MM-DD' strings.

    No dedup: unlike coros_runner.py's create_workouts.py flow, re-running
    this against dates that already have a schedule creates a SECOND entry
    rather than replacing the first — same known limitation as
    garmin_runner.py's running-workout path (Garmin side has no
    find-and-remove-same-day-same-name step). Check the calendar by hand
    before re-running for dates already scheduled.
    """
    try:
        client = get_client()
        ok = True
        for name, items, dates in workouts:
            print(name)
            ok = _process(client, name, items, dates) and ok
            print()
        return 0 if ok else 1
    except Exception as error:  # noqa: BLE001 - surface the cause plus a hint
        print(f"Garmin 操作失敗：{error}", file=sys.stderr)
        print("先確認 ~/.garminconnect/ 或 .env 裡的 GARMIN_EMAIL/GARMIN_PASSWORD", file=sys.stderr)
        return 2
