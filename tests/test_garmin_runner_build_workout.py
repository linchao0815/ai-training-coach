import importlib.util
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_GARMINCONNECT_WORKOUT_AVAILABLE = importlib.util.find_spec("garminconnect") is not None

if _GARMINCONNECT_WORKOUT_AVAILABLE:
    from garmin_runner import build_workout


@unittest.skipUnless(
    _GARMINCONNECT_WORKOUT_AVAILABLE,
    "requires the garminconnect[workout] extra (pydantic-backed typed models)",
)
class TestBuildWorkoutRepeatGroup(unittest.TestCase):
    def test_build_workout_wraps_repeat_dict_in_repeat_group(self):
        steps = [
            {"kind": "warmup", "name": "熱身", "distance_m": None, "duration_s": 900,
             "pace_fast_s": 345, "pace_slow_s": 360},
            {"kind": "repeat", "repeat_count": 5, "steps": [
                {"kind": "training", "name": "間歇", "distance_m": None, "duration_s": 240,
                 "pace_fast_s": 255, "pace_slow_s": 265},
                {"kind": "training", "name": "組間", "distance_m": None, "duration_s": 90,
                 "pace_fast_s": 390, "pace_slow_s": 420},
            ]},
        ]
        workout = build_workout("閾值課", steps)
        outer_steps = workout.workoutSegments[0].workoutSteps
        self.assertEqual(len(outer_steps), 2)
        self.assertEqual(outer_steps[0].stepOrder, 1)
        repeat_group = outer_steps[1]
        self.assertEqual(repeat_group.numberOfIterations, 5)
        self.assertEqual(len(repeat_group.workoutSteps), 2)
        self.assertEqual(repeat_group.workoutSteps[0].stepOrder, 3)
        self.assertEqual(repeat_group.workoutSteps[1].stepOrder, 4)


if __name__ == "__main__":
    unittest.main()
