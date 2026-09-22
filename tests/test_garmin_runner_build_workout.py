"""Garmin 重複組必須真的是 RepeatGroup，pydantic 1 會把它靜默轉型成 ExecutableStep。

2026-09-13 診斷：這個測試原本在完整測試套件裡是紅的，看起來像 `garmin_runner`
壞了。實際上模組是對的，是**環境**差異：

    uv run --with garminconnect              → pydantic 1.10.14
    uv run --with "garminconnect[workout]"   → pydantic 2.13.5

`WorkoutSegment.workoutSteps` 宣告成 `list[ExecutableStep | RepeatGroup]`。
pydantic 2 正確保留 RepeatGroup；**pydantic 1 沒有那種 union 支援，會把
RepeatGroup 轉型成 ExecutableStep，把 `numberOfIterations` 整個丟掉**——組出一個
沒有重複次數的壞課表，而且完全不報錯。

生產路徑本身是安全的（`create_garmin_workouts.py` 的 PEP 723 宣告了
`garminconnect[workout]`），壞的是這個測試的 skip 條件：它的訊息說需要
`[workout]` extra，實際卻只檢查 `garminconnect` 裝了沒。

所以這裡做兩件事：skip 條件改成真的檢查 pydantic ≥2；另外釘住
`_build_repeat_group()` 必須在拿到被轉型的物件時**大聲失敗**，不要靜默產出壞課表。
"""
import importlib.util
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _pydantic_v2() -> bool:
    if importlib.util.find_spec("pydantic") is None:
        return False
    import pydantic

    return int(pydantic.VERSION.split(".")[0]) >= 2


_TYPED_WORKOUTS_AVAILABLE = (
    importlib.util.find_spec("garminconnect") is not None and _pydantic_v2()
)

if _TYPED_WORKOUTS_AVAILABLE:
    import garmin_runner
    from garmin_runner import build_workout

_STEPS = [
    {"kind": "warmup", "name": "熱身", "distance_m": None, "duration_s": 900,
     "pace_fast_s": 345, "pace_slow_s": 360},
    {"kind": "repeat", "repeat_count": 5, "steps": [
        {"kind": "training", "name": "間歇", "distance_m": None, "duration_s": 240,
         "pace_fast_s": 255, "pace_slow_s": 265},
        {"kind": "training", "name": "組間", "distance_m": None, "duration_s": 90,
         "pace_fast_s": 390, "pace_slow_s": 420},
    ]},
]


@unittest.skipUnless(
    _TYPED_WORKOUTS_AVAILABLE,
    "requires garminconnect plus pydantic>=2 (the garminconnect[workout] extra) — "
    "pydantic 1 silently coerces RepeatGroup into ExecutableStep",
)
class TestBuildWorkoutRepeatGroup(unittest.TestCase):
    def test_build_workout_wraps_repeat_dict_in_repeat_group(self):
        workout = build_workout("閾值課", _STEPS)
        outer_steps = workout.workoutSegments[0].workoutSteps
        self.assertEqual(len(outer_steps), 2)
        self.assertEqual(outer_steps[0].stepOrder, 1)
        repeat_group = outer_steps[1]
        self.assertEqual(repeat_group.numberOfIterations, 5)
        self.assertEqual(len(repeat_group.workoutSteps), 2)
        self.assertEqual(repeat_group.workoutSteps[0].stepOrder, 3)
        self.assertEqual(repeat_group.workoutSteps[1].stepOrder, 4)

    def test_a_coerced_repeat_group_is_rejected_loudly(self):
        """pydantic 1 的轉型不該靜默通過——那會上傳一個沒有重複次數的課表。"""
        class Coerced:                      # 模擬被轉型成 ExecutableStep 的結果
            stepOrder = 2

        with mock.patch("garminconnect.workout.create_repeat_group",
                        return_value=Coerced()):
            with self.assertRaises(RuntimeError) as ctx:
                build_workout("閾值課", _STEPS)
        self.assertIn("pydantic", str(ctx.exception),
                      "錯誤訊息要指出真正的原因，否則下次又要從頭查一遍環境差異")


if __name__ == "__main__":
    unittest.main()
