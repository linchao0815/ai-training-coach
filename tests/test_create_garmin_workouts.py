import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coros_workout_plan import PlanEntry, Segment
from create_garmin_workouts import describe
from garmin_workout_plan import build_steps


class TestDescribeWithDuration(unittest.TestCase):
    def test_time_based_step_does_not_crash_and_shows_seconds(self):
        segment = Segment(
            name="熱身", kind="warmup", distance_m=None, duration_s=900,
            pace_fast_s=345, pace_slow_s=360,
        )
        steps = build_steps([segment])
        entry = PlanEntry(day="20260902", name="測試課表", segments=[segment])
        output = describe(entry, steps)
        self.assertIn("900秒", output.replace(" ", ""))
        self.assertIn("僅計距離型步驟", output)

    def test_mixed_distance_and_duration_steps_report_distance_total_only(self):
        segments = [
            Segment(name="熱身", kind="warmup", distance_m=None, duration_s=900,
                    pace_fast_s=345, pace_slow_s=360),
            Segment(name="馬配", kind="training", distance_m=6000, duration_s=None,
                    pace_fast_s=283, pace_slow_s=287),
        ]
        steps = build_steps(segments)
        entry = PlanEntry(day="20260902", name="測試課表", segments=segments)
        output = describe(entry, steps)
        self.assertIn("6.0km", output)


if __name__ == "__main__":
    unittest.main()
