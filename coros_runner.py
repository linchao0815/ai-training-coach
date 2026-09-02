"""
Network side of create_workouts.py: create, read back, verify, schedule.

Separate module so create_workouts.py's --dry-run path never imports coros_api
(and therefore never triggers the venv relaunch).
"""

import asyncio
import os
import sys

try:
    import coros_api
except ImportError:
    from create_workouts import _relaunch_in_tool_venv
    _relaunch_in_tool_venv()
    import coros_api

from coros_workout_plan import verify

# .env is gitignored at the repo root and, per this project's established
# convention (see garmin_auth.py's identical loader), is NOT auto-loaded by
# anything else in this repo -- shells, .mcp.json, etc. all read the real
# process environment. This is a deliberately tiny, dependency-free fallback
# (no python-dotenv) so COROS_EMAIL/COROS_PASSWORD can live in that same .env
# file instead of having to be set in every shell session by hand. It only
# fills in keys not already present in os.environ, so an explicitly-set env
# var always wins.
_DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_dotenv_defaults(path=_DOTENV_PATH):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


async def _load_auth():
    """Load stored COROS auth, falling back to env-based auto-login.

    coros_api has no `load_auth()` — this mirrors fetch_training_data.py's
    main(): get_stored_auth() first (sync), then try_auto_login() (async) if
    nothing valid is stored. COROS_EMAIL/COROS_PASSWORD can come from the real
    environment or from a gitignored .env file at the repo root (see
    _load_dotenv_defaults() above; same convention as garmin_auth.py).
    """
    _load_dotenv_defaults()
    auth = coros_api.get_stored_auth()
    if auth is None:
        auth = await coros_api.try_auto_login()
    if auth is None:
        raise RuntimeError(
            "No valid Coros auth token found (and COROS_EMAIL/COROS_PASSWORD env "
            "vars are not set for auto-login). Run `coros-mcp auth-status` / "
            "authenticate first."
        )
    return auth


async def _find_existing(auth, day, name):
    """Return scheduled entries on `day` whose workout name matches `name`."""
    scheduled = await coros_api.fetch_scheduled_workouts(auth, day, day)
    return [item for item in scheduled if item.get("workout_name") == name]


async def _remove_existing(auth, items):
    """Remove already-located scheduled entries and delete their workouts.

    Best-effort per item: one failure does not stop attempts on the rest, so a
    single bad removal can't leave the remaining old duplicates un-attempted.
    Returns the subset of `items` that could NOT be fully removed (empty list
    means everything was cleaned up).
    """
    failures = []
    for item in items:
        try:
            print(f"  發現同日同名舊排程（id_in_plan {item['id_in_plan']}），移除")
            await coros_api.remove_scheduled_workout(
                auth, item["plan_id"], item["id_in_plan"], item.get("plan_program_id") or None
            )
            workout_id = item.get("workout_id")
            if workout_id:
                print(f"  刪除舊課表 {workout_id}")
                await coros_api.delete_workout(auth, workout_id)
        except Exception as error:  # noqa: BLE001 - keep going, report at the end
            print(f"  ⚠ 移除舊排程 id_in_plan {item.get('id_in_plan')} 失敗：{error}")
            failures.append(item)
    return failures


async def _process(auth, entry, steps):
    # Look up any existing same-day same-name entry FIRST and capture its
    # identifiers now — this is the "old" entry. We schedule the new workout
    # before removing this one, so a fresh day+name lookup done *after*
    # scheduling would match BOTH the old entry and the one we just created;
    # capturing it up front avoids that trap.
    existing = await _find_existing(auth, entry.day, entry.name)

    workout_id = await coros_api.create_run_workout(auth, entry.name, steps)
    print(f"  建立課表 {workout_id}")

    stored = await coros_api.fetch_workout(auth, workout_id)
    problems = verify(stored, steps)
    if problems:
        print("  ✗ 驗證未通過：")
        for problem in problems:
            print(f"      {problem}")
        print(f"  刪除未通過驗證的課表 {workout_id}，行事曆未變動")
        await coros_api.delete_workout(auth, workout_id)
        return False

    print("  ✓ 驗證通過（配速、兩個顯示單位、估計時長）")

    # Schedule the new (verified) workout BEFORE touching the old entry. This
    # makes every failure mode benign: if schedule_workout raises, the old
    # entry is still on the calendar untouched and we just clean up the
    # orphaned new workout. The one outcome that must never happen is the day
    # ending up empty, because nothing on the watch would flag that silently.
    try:
        await coros_api.schedule_workout(auth, workout_id, entry.day, 2)
    except Exception as error:  # noqa: BLE001 - report and clean up, don't abort the run
        print(f"  ✗ 排程失敗：{error}")
        print(f"  {entry.day} 的原排程未變動")
        try:
            await coros_api.delete_workout(auth, workout_id)
            print(f"  已刪除未能排入行事曆的課表 {workout_id}")
        except Exception as cleanup_error:  # noqa: BLE001
            print(
                f"  ⚠ 清理未排入的課表 {workout_id} 也失敗：{cleanup_error} —— "
                "請手動刪除該課表（不影響行事曆，該課表未被排程）"
            )
        return False

    print(f"  排入 {entry.day}")

    if existing:
        failures = await _remove_existing(auth, existing)
        if failures:
            ids = ", ".join(str(item.get("workout_id", "?")) for item in failures)
            print(
                f"  ⚠ 新課表已成功排入，但 {len(failures)} 筆舊排程移除失敗——"
                f"{entry.day} 目前有重複排程，需手動清理舊課表（workout_id: {ids}）"
            )

    return True


# COROS 行事曆讀回的 exercise.target_type 數值：5=距離型、2=時間型
# （見 tests/test_coros_workout_plan.py 的 `_exercise()` 與
# `_real_repeat_group_exercises()` 這兩個取材自真帳號讀回的 fixture）。
# target_display_unit 只對距離型步驟有意義——build_steps() 刻意不替時間型
# 步驟設這個欄位，而真帳號讀回的時間型步驟一律帶 target_display_unit=0，
# 不是 None，所以不能只靠 `is not None` 排除掉它們。
_COROS_TARGET_TYPE_DISTANCE = 5


async def _verify_scheduled_calendar(auth, plans):
    """Verify that all scheduled workouts appear on the COROS calendar with display_unit=1."""
    if not plans:
        return True
    days = [entry.day for entry, _ in plans]
    min_day, max_day = min(days), max(days)

    print(f"=== 🔍 COROS 行事曆排程與單位上傳後覆核 ({min_day} ~ {max_day}) ===")
    try:
        scheduled = await coros_api.fetch_scheduled_workouts(auth, min_day, max_day)
    except Exception as err:
        print(f"  ⚠ 覆核查詢失敗：{err}")
        return True

    all_ok = True
    for entry, _ in plans:
        day_items = [
            item for item in (scheduled or [])
            if (item.get("happen_day") or item.get("day")) == entry.day
            and (item.get("workout_name") or item.get("name")) == entry.name
        ]
        if not day_items:
            print(f"  ❌ [{entry.day}] {entry.name} 未在 COROS 行事曆查獲排程！")
            all_ok = False
            continue

        unit_issues = []
        for item in day_items:
            workout = item.get("workout") or {}
            exercises = workout.get("exercises") or []
            for idx, ex in enumerate(exercises, 1):
                if isinstance(ex, dict) and not ex.get("is_group"):
                    # 只對距離型步驟檢查 target_display_unit——時間型步驟的這個欄位
                    # 不代表任何顯示單位問題（見上方 _COROS_TARGET_TYPE_DISTANCE 註解）。
                    if ex.get("target_type") == _COROS_TARGET_TYPE_DISTANCE:
                        if ex.get("target_display_unit") is not None and ex.get("target_display_unit") != 1:
                            unit_issues.append(f"第 {idx} 步 target_display_unit={ex.get('target_display_unit')} (非1/km)")
                    if ex.get("intensity_display_unit") is not None and ex.get("intensity_display_unit") != 1:
                        unit_issues.append(f"第 {idx} 步 intensity_display_unit={ex.get('intensity_display_unit')} (非1/km)")

        if unit_issues:
            print(f"  ⚠️ [{entry.day}] {entry.name} 存在單位疑慮: {', '.join(unit_issues)}")
            all_ok = False
        else:
            print(f"  ✓ [{entry.day}] {entry.name} 行事曆排程與顯示單位（km）覆核通過")

    print()
    return all_ok


async def _main(plans):
    auth = await _load_auth()
    ok = True
    for entry, steps in plans:
        print(f"{entry.day}  {entry.name}")
        ok = await _process(auth, entry, steps) and ok
        print()
    if ok:
        # 回傳值目前被丟棄——覆核發現的單位疑慮只會印出警告，不影響 exit code。
        # 這是既有行為，這次只修正檢查本身的誤判，沒有動退出碼語意。
        await _verify_scheduled_calendar(auth, plans)
    return 0 if ok else 1


def run(plans):
    try:
        return asyncio.run(_main(plans))
    except Exception as error:  # noqa: BLE001 - surface the cause plus a hint
        print(f"COROS 操作失敗：{error}", file=sys.stderr)
        print("先確認認證狀態：coros-mcp auth-status", file=sys.stderr)
        return 2
