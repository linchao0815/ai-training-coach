import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from garmin_strength_plan import Circuit, Exercise, build_steps, build_workout, verify


class TestBuildSteps(unittest.TestCase):
    def test_single_exercise_wraps_in_iterations_one_group(self):
        steps = build_steps([Exercise("CALF_RAISE", "STANDING_CALF_RAISE", "time", 40, rest_s=30)])
        self.assertEqual(len(steps), 1)
        group = steps[0]
        self.assertEqual(group["type"], "RepeatGroupDTO")
        self.assertEqual(group["numberOfIterations"], 1)
        self.assertTrue(group["skipLastRestStep"])
        children = group["workoutSteps"]
        self.assertEqual(len(children), 2)  # exercise + rest
        exercise, rest = children
        self.assertEqual(exercise["type"], "ExecutableStepDTO")
        self.assertEqual(exercise["stepType"]["stepTypeKey"], "interval")
        self.assertEqual(exercise["endCondition"]["conditionTypeKey"], "time")
        self.assertEqual(exercise["endConditionValue"], 40.0)
        self.assertEqual(exercise["category"], "CALF_RAISE")
        self.assertEqual(exercise["exerciseName"], "STANDING_CALF_RAISE")
        self.assertEqual(rest["stepType"]["stepTypeKey"], "rest")
        self.assertEqual(rest["endConditionValue"], 30.0)

    def test_reps_exercise_uses_reps_condition_not_iterations(self):
        # Regression: conditionTypeId 7 (iterations) is for the RepeatGroup's
        # own set count, NOT a per-exercise rep count -- easy to mix up with
        # conditionTypeId 10 (reps). See module docstring.
        steps = build_steps([Exercise("SQUAT", "SINGLE_LEG_SQUAT", "reps", 10, rest_s=45)])
        exercise = steps[0]["workoutSteps"][0]
        self.assertEqual(exercise["endCondition"]["conditionTypeId"], 10)
        self.assertEqual(exercise["endCondition"]["conditionTypeKey"], "reps")

    def test_exercise_with_no_rest_has_no_rest_step(self):
        steps = build_steps([Exercise("SQUAT", "BODYWEIGHT_WALL_SQUAT", "time", 900, rest_s=0)])
        self.assertEqual(len(steps[0]["workoutSteps"]), 1)

    def test_weight_is_attached_only_when_given(self):
        weighted = build_steps([Exercise("BENCH_PRESS", "BARBELL_BENCH_PRESS", "reps", 10, weight_kg=30)])
        exercise = weighted[0]["workoutSteps"][0]
        self.assertEqual(exercise["weightValue"], 30.0)
        self.assertEqual(exercise["weightUnit"]["unitKey"], "kilogram")

        unweighted = build_steps([Exercise("PLANK", "SIDE_PLANK", "time", 30)])
        self.assertNotIn("weightValue", unweighted[0]["workoutSteps"][0])

    def test_circuit_wraps_all_exercises_in_one_group_with_iterations(self):
        circuit = Circuit(4, [
            Exercise("BENCH_PRESS", "BARBELL_BENCH_PRESS", "reps", 10, rest_s=30, weight_kg=30),
            Exercise("DEADLIFT", "TRAP_BAR_DEADLIFT", "reps", 20, rest_s=60, weight_kg=70),
        ])
        steps = build_steps([circuit])
        self.assertEqual(len(steps), 1)  # one RepeatGroupDTO for the whole circuit
        group = steps[0]
        self.assertEqual(group["numberOfIterations"], 4)
        # 2 exercises each followed by a rest = 4 children, NOT repeated 4x in the payload
        # (COROS/Garmin both store a repeat group's children once, not physically multiplied).
        self.assertEqual(len(group["workoutSteps"]), 4)

    def test_step_order_is_continuous_across_multiple_groups(self):
        steps = build_steps([
            Exercise("CALF_RAISE", "STANDING_CALF_RAISE", "time", 40, rest_s=30),
            Exercise("CALF_RAISE", "SINGLE_LEG_STANDING_CALF_RAISE", "time", 30, rest_s=30),
        ])
        orders = []
        for group in steps:
            orders.append(group["stepOrder"])
            orders.extend(child["stepOrder"] for child in group["workoutSteps"])
        self.assertEqual(orders, sorted(set(orders)))
        self.assertEqual(orders, list(range(1, len(orders) + 1)))


class TestBuildWorkout(unittest.TestCase):
    def test_wraps_steps_in_strength_sport_type_segment(self):
        workout = build_workout("測試肌力", [Exercise("PLANK", "SIDE_PLANK", "time", 30)])
        self.assertEqual(workout["workoutName"], "測試肌力")
        self.assertEqual(workout["sportType"]["sportTypeKey"], "strength_training")
        segment = workout["workoutSegments"][0]
        self.assertEqual(segment["sportType"]["sportTypeKey"], "strength_training")
        self.assertEqual(len(segment["workoutSteps"]), 1)


def _real_calf_raise_readback():
    """De-identified excerpt of a real Garmin readback (workoutId 1706504603,
    2026-09-22) -- two exercises, each its own iterations=1 RepeatGroupDTO."""
    return {
        "workoutSegments": [{
            "workoutSteps": [
                {
                    "numberOfIterations": 1,
                    "workoutSteps": [
                        {
                            "stepType": {"stepTypeKey": "interval"},
                            "endCondition": {"conditionTypeKey": "time"},
                            "endConditionValue": 40.0,
                            "category": "CALF_RAISE",
                            "exerciseName": "STANDING_CALF_RAISE",
                        },
                        {
                            "stepType": {"stepTypeKey": "rest"},
                            "endCondition": {"conditionTypeKey": "time"},
                            "endConditionValue": 30.0,
                        },
                    ],
                },
                {
                    "numberOfIterations": 1,
                    "workoutSteps": [
                        {
                            "stepType": {"stepTypeKey": "interval"},
                            "endCondition": {"conditionTypeKey": "time"},
                            "endConditionValue": 30.0,
                            "category": "CALF_RAISE",
                            "exerciseName": "SINGLE_LEG_STANDING_CALF_RAISE",
                        },
                        {
                            "stepType": {"stepTypeKey": "rest"},
                            "endCondition": {"conditionTypeKey": "time"},
                            "endConditionValue": 30.0,
                        },
                    ],
                },
            ],
        }],
    }


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.items = [
            Exercise("CALF_RAISE", "STANDING_CALF_RAISE", "time", 40, rest_s=30),
            Exercise("CALF_RAISE", "SINGLE_LEG_STANDING_CALF_RAISE", "time", 30, rest_s=30),
        ]

    def test_matching_readback_passes_with_no_problems_or_warnings(self):
        problems, warnings = verify(_real_calf_raise_readback(), self.items)
        self.assertEqual(problems, [])
        self.assertEqual(warnings, [])

    def test_catches_step_count_mismatch(self):
        problems, _ = verify(_real_calf_raise_readback(), self.items[:1])
        self.assertTrue(any("步驟數不符" in p for p in problems))

    def test_catches_wrong_category(self):
        readback = _real_calf_raise_readback()
        readback["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]["category"] = "SQUAT"
        problems, _ = verify(readback, self.items)
        self.assertTrue(any("category 不符" in p for p in problems))

    def test_catches_wrong_value(self):
        readback = _real_calf_raise_readback()
        readback["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]["endConditionValue"] = 99.0
        problems, _ = verify(readback, self.items)
        self.assertTrue(any("數值不符" in p for p in problems))

    def test_blanked_exercise_name_is_a_warning_not_a_problem(self):
        # Regression: 2026-09-22 real account confirmed Garmin silently
        # blanks an unrecognised exerciseName instead of rejecting the
        # upload (e.g. FLYE/MACHINE_FLYE came back as FLYE/"").
        readback = _real_calf_raise_readback()
        readback["workoutSegments"][0]["workoutSteps"][0]["workoutSteps"][0]["exerciseName"] = ""
        problems, warnings = verify(readback, self.items)
        self.assertEqual(problems, [])
        self.assertTrue(any("exerciseName" in w for w in warnings))

    def test_circuit_iterations_are_expanded_before_comparison(self):
        circuit_items = [Circuit(4, [
            Exercise("BENCH_PRESS", "BARBELL_BENCH_PRESS", "reps", 10, rest_s=30, weight_kg=30),
        ])]
        readback = {
            "workoutSegments": [{
                "workoutSteps": [{
                    "numberOfIterations": 4,
                    "workoutSteps": [
                        {
                            "stepType": {"stepTypeKey": "interval"},
                            "endCondition": {"conditionTypeKey": "reps"},
                            "endConditionValue": 10.0,
                            "category": "BENCH_PRESS",
                            "exerciseName": "BARBELL_BENCH_PRESS",
                            "weightValue": 30.0,
                        },
                        {
                            "stepType": {"stepTypeKey": "rest"},
                            "endCondition": {"conditionTypeKey": "time"},
                            "endConditionValue": 30.0,
                        },
                    ],
                }],
            }],
        }
        problems, warnings = verify(readback, circuit_items)
        self.assertEqual(problems, [])
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
