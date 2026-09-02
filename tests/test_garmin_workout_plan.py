import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coros_workout_plan import RepeatGroup, Segment, parse_plan
from garmin_workout_plan import (
    GARMIN_PACE_ZONE_TARGET_TYPE_ID,
    build_steps,
    expected_duration_seconds,
    pace_range_from_speeds,
    speeds_from_pace_range,
    verify,
)


SAMPLE_DOC = """
<!-- coros: day=2026-08-09 name=長距離跑 24km 平路含4km馬配 (週日) -->

| 段落 | 距離 | 配速 |
|---|---|---|
| 熱身 | 8km | 5:40-5:55/km |
| Easy | 6km | 5:30-5:45/km |
| 馬配段 | 4km | 4:45/km |
| 收操 | 6km | 5:40-6:00/km |
"""


class TestBuildSteps(unittest.TestCase):
    def _steps(self):
        return build_steps(parse_plan(SAMPLE_DOC)[0].segments)

    def test_kinds_and_distances_carried_through(self):
        steps = self._steps()
        self.assertEqual([s["kind"] for s in steps],
                         ["warmup", "training", "training", "cooldown"])
        self.assertEqual([s["distance_m"] for s in steps], [8000, 6000, 4000, 6000])

    def test_paces_are_seconds_per_km(self):
        steps = self._steps()
        self.assertEqual(
            [(s["pace_fast_s"], s["pace_slow_s"]) for s in steps],
            [(340, 355), (330, 345), (280, 290), (340, 360)],
        )

    def test_name_includes_formatted_pace(self):
        steps = self._steps()
        self.assertEqual(steps[2]["name"], "馬配段 (4:40-4:50/km)")


class TestBuildStepsWithDuration(unittest.TestCase):
    def test_duration_segment_carries_duration_field(self):
        segment = Segment(
            name="熱身", kind="warmup", distance_m=None, duration_s=900,
            pace_fast_s=345, pace_slow_s=360,
        )
        steps = build_steps([segment])
        self.assertEqual(steps[0]["duration_s"], 900)
        self.assertIsNone(steps[0]["distance_m"])

    def test_distance_segment_unaffected(self):
        segment = Segment(
            name="馬配", kind="training", distance_m=6000, duration_s=None,
            pace_fast_s=283, pace_slow_s=287,
        )
        steps = build_steps([segment])
        self.assertEqual(steps[0]["distance_m"], 6000)
        self.assertIsNone(steps[0]["duration_s"])


class TestExpectedDurationSeconds(unittest.TestCase):
    def test_matches_distance_times_mid_pace(self):
        steps = build_steps(parse_plan(SAMPLE_DOC)[0].segments)
        # 8*347.5 + 6*337.5 + 4*285 + 6*350 = 2780+2025+1140+2100 = 8045
        self.assertAlmostEqual(expected_duration_seconds(steps), 8045.0, places=1)

    def test_time_based_step_adds_duration_directly(self):
        segment = Segment(
            name="熱身", kind="warmup", distance_m=None, duration_s=900,
            pace_fast_s=345, pace_slow_s=360,
        )
        steps = build_steps([segment])
        self.assertEqual(expected_duration_seconds(steps), 900.0)

    def test_mixed_distance_and_duration_steps_sum_correctly(self):
        segments = [
            Segment(name="熱身", kind="warmup", distance_m=None, duration_s=900,
                    pace_fast_s=345, pace_slow_s=360),
            Segment(name="馬配", kind="training", distance_m=6000, duration_s=None,
                    pace_fast_s=283, pace_slow_s=287),
        ]
        steps = build_steps(segments)
        # 900 秒（時間型）+ 6km * (283+287)/2 = 900 + 1710 = 2610
        self.assertAlmostEqual(expected_duration_seconds(steps), 2610.0, places=1)


class TestBuildStepsWithRepeatGroup(unittest.TestCase):
    def test_repeat_group_becomes_repeat_dict(self):
        inner = [
            Segment(name="間歇", kind="training", distance_m=None, duration_s=240,
                     pace_fast_s=255, pace_slow_s=265),
            Segment(name="組間", kind="training", distance_m=None, duration_s=90,
                     pace_fast_s=390, pace_slow_s=420),
        ]
        group = RepeatGroup(repeat_count=5, steps=inner)
        steps = build_steps([group])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["kind"], "repeat")
        self.assertEqual(steps[0]["repeat_count"], 5)
        self.assertEqual(len(steps[0]["steps"]), 2)
        self.assertEqual(steps[0]["steps"][0]["duration_s"], 240)
        self.assertEqual(steps[0]["steps"][1]["duration_s"], 90)

    def test_expected_duration_seconds_multiplies_by_repeat_count(self):
        inner = [
            Segment(name="間歇", kind="training", distance_m=None, duration_s=240,
                     pace_fast_s=255, pace_slow_s=265),
            Segment(name="組間", kind="training", distance_m=None, duration_s=90,
                     pace_fast_s=390, pace_slow_s=420),
        ]
        steps = build_steps([RepeatGroup(repeat_count=5, steps=inner)])
        self.assertEqual(expected_duration_seconds(steps), 5 * (240 + 90))


class TestPaceSpeedConversion(unittest.TestCase):
    def test_speeds_from_pace_range(self):
        # 5:00-5:10/km -> 慢的 310s/km 對應較低速度，快的 300s/km 對應較高速度
        min_speed, max_speed = speeds_from_pace_range(300, 310)
        self.assertAlmostEqual(min_speed, 1000.0 / 310, places=6)
        self.assertAlmostEqual(max_speed, 1000.0 / 300, places=6)

    def test_pace_range_from_speeds_is_inverse(self):
        min_speed, max_speed = speeds_from_pace_range(300, 310)
        self.assertEqual(pace_range_from_speeds(min_speed, max_speed), (300, 310))

    def test_round_trip_on_all_sample_segments(self):
        for fast_s, slow_s in [(340, 355), (330, 345), (280, 290), (340, 360)]:
            min_speed, max_speed = speeds_from_pace_range(fast_s, slow_s)
            self.assertEqual(pace_range_from_speeds(min_speed, max_speed), (fast_s, slow_s))


def _garmin_step(distance_m, pace_fast_s, pace_slow_s, target_type_id=6):
    min_speed, max_speed = speeds_from_pace_range(pace_fast_s, pace_slow_s)
    return {
        "type": "ExecutableStepDTO",
        "endConditionValue": float(distance_m),
        "targetType": {"workoutTargetTypeId": target_type_id, "workoutTargetTypeKey": "pace.zone"},
        "targetValueOne": min_speed,
        "targetValueTwo": max_speed,
    }


def _good_workout(steps):
    garmin_steps = [_garmin_step(s["distance_m"], s["pace_fast_s"], s["pace_slow_s"]) for s in steps]
    return {
        "workoutSegments": [{"workoutSteps": garmin_steps}],
        "estimatedDurationInSecs": round(expected_duration_seconds(steps)),
    }


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.steps = build_steps(parse_plan(SAMPLE_DOC)[0].segments)

    def test_accepts_a_correct_workout(self):
        self.assertEqual(verify(_good_workout(self.steps), self.steps), [])

    def test_catches_step_count_mismatch(self):
        workout = _good_workout(self.steps)
        workout["workoutSegments"][0]["workoutSteps"].pop()
        problems = verify(workout, self.steps)
        self.assertTrue(any("步驟數" in p for p in problems), problems)

    def test_catches_wrong_distance(self):
        workout = _good_workout(self.steps)
        workout["workoutSegments"][0]["workoutSteps"][2]["endConditionValue"] = 3000.0
        problems = verify(workout, self.steps)
        self.assertTrue(any("距離不符" in p for p in problems), problems)
        self.assertTrue(any("馬配段" in p for p in problems), problems)

    def test_catches_wrong_target_type(self):
        workout = _good_workout(self.steps)
        workout["workoutSegments"][0]["workoutSteps"][0]["targetType"] = {"workoutTargetTypeId": 1}
        problems = verify(workout, self.steps)
        self.assertTrue(any("targetType" in p for p in problems), problems)

    def test_catches_wrong_pace(self):
        workout = _good_workout(self.steps)
        workout["workoutSegments"][0]["workoutSteps"][2]["targetValueOne"] = 1.0
        workout["workoutSegments"][0]["workoutSteps"][2]["targetValueTwo"] = 1.0
        problems = verify(workout, self.steps)
        self.assertTrue(any("配速不符" in p for p in problems), problems)

    def test_catches_all_paces_collapsed_to_one_value(self):
        workout = _good_workout(self.steps)
        collapsed = _garmin_step(1000, 300, 310)
        for step in workout["workoutSegments"][0]["workoutSteps"]:
            step["targetValueOne"] = collapsed["targetValueOne"]
            step["targetValueTwo"] = collapsed["targetValueTwo"]
        problems = verify(workout, self.steps)
        self.assertTrue(any("配速換算後相同" in p for p in problems), problems)

    def test_catches_implausible_estimated_duration(self):
        workout = _good_workout(self.steps)
        workout["estimatedDurationInSecs"] = 999999
        problems = verify(workout, self.steps)
        self.assertTrue(any("estimatedDurationInSecs" in p for p in problems), problems)

    def test_handles_empty_workout_dict(self):
        problems = verify({}, self.steps)
        self.assertTrue(any("步驟數" in p for p in problems), problems)


class TestVerifyWithDuration(unittest.TestCase):
    def setUp(self):
        segment = Segment(
            name="熱身", kind="warmup", distance_m=None, duration_s=900,
            pace_fast_s=345, pace_slow_s=360,
        )
        self.steps = build_steps([segment])

    def _workout(self, end_condition_value):
        step = _garmin_step(end_condition_value, 345, 360)
        return {
            "workoutSegments": [{"workoutSteps": [step]}],
            "estimatedDurationInSecs": 900,
        }

    def test_accepts_a_correct_time_based_workout(self):
        self.assertEqual(verify(self._workout(900), self.steps), [])

    def test_catches_mismatched_duration(self):
        problems = verify(self._workout(600), self.steps)
        self.assertTrue(problems)
        self.assertTrue(any("時長不符" in p for p in problems), problems)


class TestVerifyRepeatGroup(unittest.TestCase):
    def test_expands_repeat_group_for_comparison(self):
        expected = [{
            "kind": "repeat", "repeat_count": 2,
            "steps": [{"kind": "training", "name": "間歇", "distance_m": None,
                       "duration_s": 240, "pace_fast_s": 255, "pace_slow_s": 265}],
        }]
        inner_step = {
            "type": "ExecutableStepDTO",
            "endConditionValue": 240.0,
            "targetType": {"workoutTargetTypeId": GARMIN_PACE_ZONE_TARGET_TYPE_ID},
            "targetValueOne": 1000.0 / 265, "targetValueTwo": 1000.0 / 255,
        }
        workout = {
            "workoutSegments": [{"workoutSteps": [
                {"type": "RepeatGroupDTO", "numberOfIterations": 2,
                 "workoutSteps": [inner_step]},
            ]}],
            "estimatedDurationInSecs": 480,
        }
        problems = verify(workout, expected)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
