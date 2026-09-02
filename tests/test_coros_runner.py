"""
Pins the ordering that _process() in coros_runner.py depends on for safety:
schedule the new workout before touching any old same-day entry, so a failure
partway through never leaves the calendar day empty (incident #3, 2026-08-02).

coros_runner.py imports the real `coros_api` (a vendor package) at module load.
To keep this suite running under plain `python3` with no venv, we inject a
fake `coros_api` module into sys.modules BEFORE importing coros_runner, so the
`import coros_api` at the top of that module binds our fake instead of the
real vendor package. coros_workout_plan.py stays untouched (stdlib-only) and
is imported directly as usual.
"""

import asyncio
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coros_workout_plan import PlanEntry, expected_duration_seconds  # noqa: E402


class FakeState:
    """Mutable recorder + canned responses read by the fake coros_api functions."""

    def __init__(self):
        self.calls = []
        self.existing = []
        self.next_workout_id = "NEW1"
        self.stored_workout = {}
        self.schedule_error = None


# Swapped out per-test via setUp; the fake module's functions always read the
# current box contents, so we don't need to reload coros_runner between tests.
_state_box = [FakeState()]


def _install_fake_coros_api():
    module = types.ModuleType("coros_api")

    def get_stored_auth():
        # Real coros_api: sync, not a coroutine (coros_runner calls it unawaited).
        return "AUTH"

    async def try_auto_login():
        return "AUTH"

    async def fetch_scheduled_workouts(auth, start_day, end_day):
        _state_box[0].calls.append("fetch_scheduled_workouts")
        return _state_box[0].existing

    async def create_run_workout(auth, name, steps):
        _state_box[0].calls.append("create_run_workout")
        return _state_box[0].next_workout_id

    async def fetch_workout(auth, workout_id):
        _state_box[0].calls.append("fetch_workout")
        return _state_box[0].stored_workout

    async def schedule_workout(auth, workout_id, day, position):
        _state_box[0].calls.append("schedule_workout")
        if _state_box[0].schedule_error is not None:
            raise _state_box[0].schedule_error

    async def remove_scheduled_workout(auth, plan_id, id_in_plan, plan_program_id):
        _state_box[0].calls.append("remove_scheduled_workout")

    async def delete_workout(auth, workout_id):
        _state_box[0].calls.append(f"delete_workout:{workout_id}")

    module.get_stored_auth = get_stored_auth
    module.try_auto_login = try_auto_login
    module.fetch_scheduled_workouts = fetch_scheduled_workouts
    module.create_run_workout = create_run_workout
    module.fetch_workout = fetch_workout
    module.schedule_workout = schedule_workout
    module.remove_scheduled_workout = remove_scheduled_workout
    module.delete_workout = delete_workout
    return module


sys.modules["coros_api"] = _install_fake_coros_api()

import coros_runner  # noqa: E402  must come after the fake coros_api injection


def _make_step(distance_m=8000, fast=340, slow=355, name="熱身 (5:40-5:55/km)", kind="warmup"):
    return {
        "kind": kind,
        "name": name,
        "target_type": "distance",
        "target_distance_meters": distance_m,
        "target_display_unit": 1,
        "intensity_type": 3,
        "is_intensity_percent": False,
        "intensity_value": fast,
        "intensity_value_extend": slow,
        "intensity_display_unit": 1,
    }


def _matching_exercise(step):
    return {
        "name": step["name"],
        "target_value": step["target_distance_meters"] * 100,
        "target_display_unit": step["target_display_unit"],
        "intensity_type": step["intensity_type"],
        "intensity_value": step["intensity_value"],
        "intensity_value_extend": step["intensity_value_extend"],
        "intensity_display_unit": step["intensity_display_unit"],
        "is_group": False,
    }


def _good_stored_workout(steps):
    return {
        "estimated_time_seconds": expected_duration_seconds(steps),
        "exercises": [_matching_exercise(s) for s in steps],
    }


_OLD_ENTRY = {
    "id_in_plan": 1,
    "plan_id": "P1",
    "plan_program_id": None,
    "workout_id": "OLD1",
    "workout_name": "長跑",  # must match entry.name: _find_existing filters on this
}


class TestProcessOrdering(unittest.TestCase):
    """Guards the fix for incident #3: schedule the new workout before removing
    the old one, so every failure mode leaves the calendar day non-empty."""

    def setUp(self):
        _state_box[0] = FakeState()
        self.steps = [_make_step()]
        self.entry = PlanEntry(day="20260809", name="長跑", segments=[])

    def test_success_path_order_is_create_fetch_schedule_remove(self):
        _state_box[0].existing = [dict(_OLD_ENTRY)]
        _state_box[0].stored_workout = _good_stored_workout(self.steps)

        ok = asyncio.run(coros_runner._process("AUTH", self.entry, self.steps))

        self.assertTrue(ok)
        self.assertEqual(_state_box[0].calls, [
            "fetch_scheduled_workouts",
            "create_run_workout",
            "fetch_workout",
            "schedule_workout",
            "remove_scheduled_workout",
            "delete_workout:OLD1",
        ])

    def test_schedule_failure_does_not_remove_and_deletes_new_workout(self):
        _state_box[0].existing = [dict(_OLD_ENTRY)]
        _state_box[0].stored_workout = _good_stored_workout(self.steps)
        _state_box[0].schedule_error = RuntimeError("boom")

        ok = asyncio.run(coros_runner._process("AUTH", self.entry, self.steps))

        self.assertFalse(ok)
        self.assertNotIn("remove_scheduled_workout", _state_box[0].calls)
        self.assertIn("delete_workout:NEW1", _state_box[0].calls)
        # The old entry must never be touched: the last action is cleaning up
        # the orphaned new workout, not removing the still-valid old one.
        self.assertEqual(_state_box[0].calls[-1], "delete_workout:NEW1")

    def test_verification_failure_does_not_schedule_or_remove_and_deletes_new_workout(self):
        _state_box[0].existing = [dict(_OLD_ENTRY)]
        bad = _good_stored_workout(self.steps)
        bad["exercises"][0]["intensity_value"] = 999  # wrong pace -> verify() fails
        _state_box[0].stored_workout = bad

        ok = asyncio.run(coros_runner._process("AUTH", self.entry, self.steps))

        self.assertFalse(ok)
        self.assertNotIn("schedule_workout", _state_box[0].calls)
        self.assertNotIn("remove_scheduled_workout", _state_box[0].calls)
        self.assertIn("delete_workout:NEW1", _state_box[0].calls)


if __name__ == "__main__":
    unittest.main()
