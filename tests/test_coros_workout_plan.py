import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from coros_workout_plan import (
    PlanEntry,
    RepeatGroup,
    Segment,
    build_steps,
    clean_cell,
    expected_duration_seconds,
    parse_distance,
    parse_distance_or_duration,
    parse_duration,
    parse_pace,
    parse_plan,
    verify,
    _parse_repeat_block,
)


class TestCleanCell(unittest.TestCase):
    def test_strips_bold_markers(self):
        self.assertEqual(clean_cell("**4km**"), "4km")

    def test_strips_fullwidth_parenthetical(self):
        self.assertEqual(clean_cell("**4:45/km**（不追 4:37）"), "4:45/km")

    def test_strips_halfwidth_parenthetical(self):
        self.assertEqual(clean_cell("8km (含起步)"), "8km")

    def test_leaves_plain_text_alone(self):
        self.assertEqual(clean_cell("  5:30-5:45/km  "), "5:30-5:45/km")


class TestParseDistance(unittest.TestCase):
    def test_integer_km(self):
        self.assertEqual(parse_distance("8km"), 8000)

    def test_space_before_unit(self):
        self.assertEqual(parse_distance("6 km"), 6000)

    def test_decimal_km(self):
        self.assertEqual(parse_distance("2.5km"), 2500)

    def test_rejects_cumulative_range(self):
        # 累積區間是舊格式，必須明確拒絕而不是猜
        with self.assertRaises(ValueError) as ctx:
            parse_distance("0-8km")
        self.assertIn("0-8km", str(ctx.exception))

    def test_rejects_missing_unit(self):
        with self.assertRaises(ValueError):
            parse_distance("8")


class TestParsePace(unittest.TestCase):
    def test_range(self):
        self.assertEqual(parse_pace("5:40-5:55/km"), (340, 355))

    def test_marathon_pace_range(self):
        self.assertEqual(parse_pace("4:40-4:50/km"), (280, 290))

    def test_single_value_expands_by_five_seconds(self):
        self.assertEqual(parse_pace("4:45/km"), (280, 290))

    def test_rejects_reversed_range(self):
        with self.assertRaises(ValueError):
            parse_pace("5:55-5:40/km")

    def test_rejects_missing_unit(self):
        with self.assertRaises(ValueError):
            parse_pace("5:40-5:55")


class TestParseDuration(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(parse_duration("15分鐘"), 900)

    def test_minutes_single_digit(self):
        self.assertEqual(parse_duration("4分鐘"), 240)

    def test_seconds(self):
        self.assertEqual(parse_duration("90秒"), 90)

    def test_rejects_km(self):
        with self.assertRaises(ValueError):
            parse_duration("8km")

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_duration("十分鐘")


class TestParseDistanceOrDuration(unittest.TestCase):
    def test_distance(self):
        self.assertEqual(parse_distance_or_duration("8km"), ("distance", 8000))

    def test_duration_minutes(self):
        self.assertEqual(parse_distance_or_duration("15分鐘"), ("duration", 900))

    def test_duration_seconds(self):
        self.assertEqual(parse_distance_or_duration("90秒"), ("duration", 90))

    def test_rejects_unrecognized(self):
        with self.assertRaises(ValueError):
            parse_distance_or_duration("8哩")


SAMPLE_DOC = """
# 某週計畫

隨便一段前言，不該被解析。

### 08-09 長跑分段（重點課）

<!-- coros: day=2026-08-09 name=長距離跑 24km 平路含4km馬配 (週日) -->

| 段落 | 距離 | 配速 | 目的 |
|---|---|---|---|
| 熱身 | 8km | 5:40-5:55/km | 不要衝開頭 |
| Easy | 6km | 5:30-5:45/km | 過渡 |
| **馬配段** | **4km** | **4:45/km**（不追 4:37） | 鎖速 |
| 收操 | 6km | 5:40-6:00/km | 觀察跑姿 |

後面還有別的段落。
"""


class TestParsePlan(unittest.TestCase):
    def test_extracts_single_entry(self):
        entries = parse_plan(SAMPLE_DOC)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].day, "20260809")
        self.assertEqual(entries[0].name, "長距離跑 24km 平路含4km馬配 (週日)")

    def test_extracts_all_four_segments(self):
        segments = parse_plan(SAMPLE_DOC)[0].segments
        self.assertEqual(len(segments), 4)
        self.assertEqual(
            [(s.distance_m, s.pace_fast_s, s.pace_slow_s) for s in segments],
            [(8000, 340, 355), (6000, 330, 345), (4000, 280, 290), (6000, 340, 360)],
        )

    def test_infers_kind_by_position_and_name(self):
        segments = parse_plan(SAMPLE_DOC)[0].segments
        self.assertEqual([s.kind for s in segments],
                         ["warmup", "training", "training", "cooldown"])

    def test_segment_names_are_cleaned(self):
        segments = parse_plan(SAMPLE_DOC)[0].segments
        self.assertEqual(segments[2].name, "馬配段")

    def test_handles_multiple_anchors(self):
        doc = SAMPLE_DOC + """
<!-- coros: day=2026-08-05 name=閾值課 -->

| 段落 | 距離 | 配速 |
|---|---|---|
| 熱身 | 3km | 5:45-6:00/km |
| 主段 | 5km | 4:35-4:45/km |
"""
        entries = parse_plan(doc)
        self.assertEqual([e.day for e in entries], ["20260809", "20260805"])
        self.assertEqual(len(entries[1].segments), 2)

    def test_raises_when_no_anchor(self):
        with self.assertRaises(ValueError) as ctx:
            parse_plan("# 沒有錨點的文件\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        self.assertIn("coros:", str(ctx.exception))

    def test_raises_when_required_column_missing(self):
        doc = """
<!-- coros: day=2026-08-09 name=X -->

| 段落 | 時間 |
|---|---|
| 熱身 | 30min |
"""
        with self.assertRaises(ValueError) as ctx:
            parse_plan(doc)
        self.assertIn("配速", str(ctx.exception))

    def test_two_row_table_second_row_is_training_not_cooldown(self):
        # 兩列表格：第 2 列（主段）不該被位置推斷誤標成 cooldown。
        doc = """
<!-- coros: day=2026-08-05 name=兩段課 -->

| 段落 | 距離 | 配速 |
|---|---|---|
| 熱身段 | 3km | 5:45-6:00/km |
| 主段 | 5km | 4:35-4:45/km |
"""
        segments = parse_plan(doc)[0].segments
        self.assertEqual([s.kind for s in segments], ["warmup", "training"])

    def test_raises_when_no_table_after_anchor(self):
        with self.assertRaises(ValueError) as ctx:
            parse_plan("<!-- coros: day=2026-08-09 name=X -->\n\n沒有表格。\n")
        self.assertIn("表格", str(ctx.exception))


class TestParseRealPlanDoc(unittest.TestCase):
    """契約測試：範本自帶的範例文件必須解析得出來。文件改格式時這個測試要一起改。"""

    def test_example_doc_parses(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "doc", "example-training-plan.md")
        with open(path, encoding="utf-8") as handle:
            entries = parse_plan(handle.read())
        self.assertEqual(len(entries), 2)
        long_run = next(e for e in entries if e.day == "20261220")
        self.assertEqual(
            [s.distance_m for s in long_run.segments], [6000, 10000, 6000, 2000]
        )
        self.assertEqual(sum(s.distance_m for s in long_run.segments), 24000)


class TestBuildSteps(unittest.TestCase):
    def _steps(self):
        return build_steps(parse_plan(SAMPLE_DOC)[0].segments)

    def test_every_step_uses_kilometre_display_units(self):
        # 這是本專案最容易再犯的錯：coros_api 兩個欄位都預設 3（英里）。
        for index, step in enumerate(self._steps(), 1):
            self.assertEqual(step["target_display_unit"], 1,
                             f"第 {index} 步的 target_display_unit 不是公里")
            self.assertEqual(step["intensity_display_unit"], 1,
                             f"第 {index} 步的 intensity_display_unit 不是公里")

    def test_paces_are_seconds_per_km(self):
        steps = self._steps()
        self.assertEqual(
            [(s["intensity_value"], s["intensity_value_extend"]) for s in steps],
            [(340, 355), (330, 345), (280, 290), (340, 360)],
        )

    def test_distances_are_metres(self):
        self.assertEqual([s["target_distance_meters"] for s in self._steps()],
                         [8000, 6000, 4000, 6000])

    def test_intensity_type_is_pace(self):
        for step in self._steps():
            self.assertEqual(step["intensity_type"], 3)
            self.assertIs(step["is_intensity_percent"], False)

    def test_kinds_and_names_carried_through(self):
        steps = self._steps()
        self.assertEqual([s["kind"] for s in steps],
                         ["warmup", "training", "training", "cooldown"])
        self.assertEqual(steps[2]["name"], "馬配段 (4:40-4:50/km)")

    def test_never_emits_a_pace_string_field(self):
        # coros-training-mcp 的 `pace` 參數會靜默塌成 preset 預設值，絕不使用。
        for step in self._steps():
            self.assertNotIn("pace", step)
            self.assertNotIn("intensity_label", step)


class TestBuildStepsWithDuration(unittest.TestCase):
    def test_duration_segment_becomes_duration_step(self):
        segment = Segment(
            name="熱身", kind="warmup", distance_m=None, duration_s=900,
            pace_fast_s=345, pace_slow_s=360,
        )
        steps = build_steps([segment])
        self.assertEqual(steps[0]["target_type"], "time")
        self.assertEqual(steps[0]["target_duration_seconds"], 900)
        self.assertNotIn("target_distance_meters", steps[0])

    def test_distance_segment_unaffected(self):
        segment = Segment(
            name="馬配", kind="training", distance_m=6000, duration_s=None,
            pace_fast_s=283, pace_slow_s=287,
        )
        steps = build_steps([segment])
        self.assertEqual(steps[0]["target_type"], "distance")
        self.assertEqual(steps[0]["target_distance_meters"], 6000)
        self.assertNotIn("target_duration_seconds", steps[0])


class TestExpectedDurationSecondsWithDuration(unittest.TestCase):
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


def _exercise(intensity_value, extend, target_unit=1, intensity_unit=1, distance=800000):
    return {
        "name": "步驟",
        "target_type": 5,
        "target_value": distance,
        "target_display_unit": target_unit,
        "intensity_type": 3,
        "intensity_value": intensity_value,
        "intensity_value_extend": extend,
        "intensity_display_unit": intensity_unit,
        "is_group": False,
    }


def _good_workout(steps):
    exercises = [
        _exercise(s["intensity_value"], s["intensity_value_extend"],
                  distance=s["target_distance_meters"] * 100)
        for s in steps
    ]
    return {"estimated_time_seconds": 8045, "exercises": exercises}


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.steps = build_steps(parse_plan(SAMPLE_DOC)[0].segments)

    def test_accepts_a_correct_workout(self):
        self.assertEqual(verify(_good_workout(self.steps), self.steps), [])

    def test_catches_the_pace_parameter_bug(self):
        # 2026-08-02 實際形狀：四步全部塌成 Pace preset 預設值。
        workout = {
            "estimated_time_seconds": 4921260,
            "exercises": [_exercise(186411, 223694) for _ in self.steps],
        }
        problems = verify(workout, self.steps)
        self.assertTrue(problems)
        self.assertTrue(any("配速相同" in p for p in problems), problems)

    def test_catches_the_miles_bug(self):
        # 2026-08-02 實際形狀：配速對了，但距離顯示單位是 3（英里）。
        workout = _good_workout(self.steps)
        for exercise in workout["exercises"]:
            exercise["target_display_unit"] = 3
        problems = verify(workout, self.steps)
        self.assertTrue(problems)
        self.assertTrue(any("target_display_unit" in p for p in problems), problems)
        self.assertTrue(any("英里" in p for p in problems), problems)

    def test_catches_wrong_intensity_display_unit(self):
        workout = _good_workout(self.steps)
        workout["exercises"][0]["intensity_display_unit"] = 2
        problems = verify(workout, self.steps)
        self.assertTrue(any("intensity_display_unit" in p for p in problems), problems)

    def test_catches_wrong_pace_value(self):
        workout = _good_workout(self.steps)
        workout["exercises"][2]["intensity_value"] = 300
        problems = verify(workout, self.steps)
        self.assertTrue(any("intensity_value" in p for p in problems), problems)

    def test_catches_wrong_stored_distance(self):
        # 4km 馬配段被存成 3km，但配速、單位、估計時長都恰好沒觸發其他檢查。
        workout = _good_workout(self.steps)
        workout["exercises"][2]["target_value"] = self.steps[2]["target_distance_meters"] * 100 - 100000
        problems = verify(workout, self.steps)
        self.assertTrue(any("target_value" in p for p in problems), problems)
        self.assertTrue(any("馬配段" in p for p in problems), problems)

    def test_accepts_correct_stored_distance(self):
        # target_value 以公分儲存：target_distance_meters * 100。
        problems = verify(_good_workout(self.steps), self.steps)
        self.assertEqual(problems, [])

    def test_catches_step_count_mismatch(self):
        workout = _good_workout(self.steps)
        workout["exercises"].pop()
        problems = verify(workout, self.steps)
        self.assertTrue(any("步驟數" in p for p in problems), problems)

    def test_catches_implausible_estimated_time(self):
        workout = _good_workout(self.steps)
        workout["estimated_time_seconds"] = 4921260
        problems = verify(workout, self.steps)
        self.assertTrue(any("estimated_time_seconds" in p for p in problems), problems)

    def test_group_row_excluded_from_per_step_comparison(self):
        # is_group=True rows must never be zipped against an expected training
        # step (that would misattribute a real problem to the wrong label, or
        # spuriously compare a group container's absent pace fields). This plan
        # has no RepeatGroup, so an unexpected group row is flagged on its own
        # (分組數不符) but must not also corrupt the per-step comparison below it.
        workout = _good_workout(self.steps)
        workout["exercises"].insert(0, {"name": "組", "is_group": True})
        problems = verify(workout, self.steps)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("分組數不符", problems[0])

    def test_handles_exercises_is_none(self):
        # Regression: exercises key present with None value should not crash
        workout = {"estimated_time_seconds": 8045, "exercises": None}
        problems = verify(workout, self.steps)
        self.assertTrue(problems)
        self.assertTrue(any("步驟數" in p for p in problems), problems)

    def test_handles_exercises_contains_none(self):
        # Regression: exercises list with None elements should not crash
        workout = {"estimated_time_seconds": 8045, "exercises": [None]}
        problems = verify(workout, self.steps)
        self.assertTrue(problems)
        self.assertTrue(any("步驟數" in p for p in problems), problems)

    def test_handles_empty_workout_dict(self):
        # Regression: empty workout dict should report problems, not crash
        problems = verify({}, self.steps)
        self.assertTrue(problems)


def _duration_exercise(intensity_value, extend, duration_seconds, intensity_unit=1):
    # `duration_seconds` -- confirmed real readback field name for time-based
    # steps (Task 7, 2026-09-02 real account fetch). `target_duration_seconds`
    # is the OUTGOING build_steps() field name only; the two differ.
    return {
        "name": "步驟",
        "intensity_type": 3,
        "intensity_value": intensity_value,
        "intensity_value_extend": extend,
        "intensity_display_unit": intensity_unit,
        "duration_seconds": duration_seconds,
        "is_group": False,
    }


class TestVerifyWithDuration(unittest.TestCase):
    def setUp(self):
        segment = Segment(
            name="熱身", kind="warmup", distance_m=None, duration_s=900,
            pace_fast_s=345, pace_slow_s=360,
        )
        self.steps = build_steps([segment])

    def test_accepts_a_correct_time_based_workout(self):
        workout = {
            "estimated_time_seconds": 900,
            "exercises": [_duration_exercise(345, 360, 900)],
        }
        self.assertEqual(verify(workout, self.steps), [])

    def test_catches_mismatched_duration(self):
        workout = {
            "estimated_time_seconds": 900,
            "exercises": [_duration_exercise(345, 360, 600)],
        }
        problems = verify(workout, self.steps)
        self.assertTrue(problems)
        self.assertTrue(any("duration_seconds" in p for p in problems), problems)

    def test_does_not_check_distance_fields_for_time_based_step(self):
        # 沒有 target_display_unit/target_value 也不該觸發距離相關檢查。
        workout = {
            "estimated_time_seconds": 900,
            "exercises": [_duration_exercise(345, 360, 900)],
        }
        problems = verify(workout, self.steps)
        self.assertFalse(any("target_display_unit" in p for p in problems), problems)
        self.assertFalse(any("target_value" in p for p in problems), problems)


class TestRepeatBlock(unittest.TestCase):
    def test_parses_block_into_repeat_group(self):
        lines = [
            ">>> 重複 5 組",
            "| 間歇 | 4分鐘 | 4:15-4:25/km |",
            "| 組間 | 90秒 | 6:30-7:00/km |",
            "<<<",
        ]
        group, next_index = _parse_repeat_block(
            lines, 0, day="20260902", name_col=0, distance_col=1, pace_col=2
        )
        self.assertIsInstance(group, RepeatGroup)
        self.assertEqual(group.repeat_count, 5)
        self.assertEqual(next_index, 4)
        self.assertEqual(len(group.steps), 2)
        self.assertEqual(group.steps[0].name, "間歇")
        self.assertEqual(group.steps[0].duration_s, 240)
        self.assertEqual(group.steps[0].kind, "training")
        self.assertEqual(group.steps[1].name, "組間")
        self.assertEqual(group.steps[1].duration_s, 90)

    def test_missing_close_marker_raises(self):
        lines = [">>> 重複 5 組", "| 間歇 | 4分鐘 | 4:15-4:25/km |"]
        with self.assertRaises(ValueError):
            _parse_repeat_block(lines, 0, day="20260902", name_col=0, distance_col=1, pace_col=2)

    def test_empty_block_raises(self):
        lines = [">>> 重複 5 組", "<<<"]
        with self.assertRaises(ValueError):
            _parse_repeat_block(lines, 0, day="20260902", name_col=0, distance_col=1, pace_col=2)


class TestParsePlanWithRepeatBlock(unittest.TestCase):
    def test_mixed_flat_and_repeat_segments(self):
        doc = """
<!-- coros: day=2026-09-02 name=閾值課 -->
| 名稱 | 距離 | 配速 |
|---|---|---|
| 熱身 | 15分鐘 | 5:45-6:00/km |
>>> 重複 5 組
| 間歇 | 4分鐘 | 4:15-4:25/km |
| 組間 | 90秒 | 6:30-7:00/km |
<<<
| 收操 | 10分鐘 | 5:45-6:00/km |
"""
        entries = parse_plan(doc)
        self.assertEqual(len(entries), 1)
        segments = entries[0].segments
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0].name, "熱身")
        self.assertEqual(segments[0].kind, "warmup")
        self.assertIsInstance(segments[1], RepeatGroup)
        self.assertEqual(segments[1].repeat_count, 5)
        self.assertEqual(segments[2].name, "收操")
        self.assertEqual(segments[2].kind, "cooldown")

    def test_two_anchors_each_with_repeat_block(self):
        doc = """
<!-- coros: day=2026-09-02 name=課A -->
| 名稱 | 距離 | 配速 |
|---|---|---|
>>> 重複 3 組
| 間歇 | 2分鐘 | 4:00-4:10/km |
<<<

<!-- coros: day=2026-09-05 name=課B -->
| 名稱 | 距離 | 配速 |
|---|---|---|
| 熱身 | 2km | 5:30-5:50/km |
"""
        entries = parse_plan(doc)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].day, "20260902")
        self.assertIsInstance(entries[0].segments[0], RepeatGroup)
        self.assertEqual(entries[1].day, "20260905")
        self.assertEqual(len(entries[1].segments), 1)

    def test_existing_flat_only_docs_still_work(self):
        # 既有 W30-W35 那種「一個錨點一張純表格」文件必須完全不受影響。
        doc = """
<!-- coros: day=2026-08-31 name=長跑 -->
| 名稱 | 距離 | 配速 |
|---|---|---|
| 熱身 | 2km | 5:30-5:50/km |
| 馬配 | 6km | 4:43-4:47/km |
| 收操 | 2km | 5:30-5:50/km |
"""
        entries = parse_plan(doc)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0].segments), 3)
        self.assertTrue(all(isinstance(s, Segment) for s in entries[0].segments))


class TestParsePlanTrailingTableRegression(unittest.TestCase):
    """C1 回歸測試：`_collect_entry_segments()` 曾經把「最後一個錨點」之後、
    檔案裡緊接著的無關表格（例如「與 W34 的差異」這種週次比較表）也吞進最後
    一堂課的分段，導致該表的表頭列被當成資料列丟給 parse_distance_or_duration()
    解析，炸出 `ValueError: 無法解析距離或時間長度 'W34'`。真實觸發場景是
    doc/2026-W33/34/35-training-plan.md（表格後面接一個 `####` 標題再接另一張
    表），而不是舊測試 test_existing_flat_only_docs_still_work 那種「錨點→表格
    →結束」的簡化形狀，這也是這個回歸滑過 9 輪 task review 才被抓到的原因。"""

    def test_all_real_training_plan_docs_parse_without_raising(self):
        doc_dir = os.path.join(os.path.dirname(__file__), "..", "doc")
        paths = sorted(glob.glob(os.path.join(doc_dir, "*-training-plan.md")))
        self.assertTrue(paths, "找不到任何 doc/*-training-plan.md，glob pattern 可能失效")
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            if "<!-- coros:" not in text:
                # 已完全同步過、錨點已被清空的舊課表（例如 W30/W31），
                # parse_plan() 對它們必然拋「找不到錨點」，不是這裡要驗證的範圍。
                continue
            try:
                parse_plan(text)
            except Exception as exc:  # noqa: BLE001 - 刻意攔截任何解析例外並附上檔名
                self.fail(f"{os.path.basename(path)} 解析失敗：{exc!r}")

    def test_trailing_unrelated_table_after_heading_is_excluded(self):
        # 重現真實課表文件形狀：錨點 -> 課表分段表 -> `####` 標題 -> 無關的
        # 週次比較表。錨點後只有一堂課，比較表不該被算進它的分段。
        doc = """
<!-- coros: day=2026-08-29 name=長距離跑 26km 平路含6km馬配 (週六) -->
| 段落 | 距離 | 配速 | 目的 |
|---|---|---|---|
| 熱身 | 8km | 5:15-5:35/km | 依體感跑 |
| 馬配段 | 6km | 4:43-4:47/km | 本週唯一的加量 |
| 收操 | 6km | 5:30-5:50/km | HR 回落 |

#### 與 W34 的差異

| 項目 | W34 | W35 | 變更理由 |
|---|---|---|---|
| 馬配段距離 | 5km | 6km | 步頻步幅整段持平 |
| 收操 | 7km | 6km | 馬配多 1km，總距離守住 26km |
"""
        entries = parse_plan(doc)
        self.assertEqual(len(entries), 1)
        segments = entries[0].segments
        self.assertEqual(len(segments), 3)
        names = [s.name for s in segments]
        self.assertEqual(names, ["熱身", "馬配段", "收操"])
        self.assertNotIn("項目", names)
        self.assertNotIn("馬配段距離", names)


def _repeat_group_segments():
    """Mirrors the real 2026-09-02 5x4min threshold workout (doc/2026-W36-training-plan.md
    "09-02 閾值課重新校準"): 15min warmup, 5x(4min @ 4:15-4:25/km + 90s jog recovery
    @ 6:30-7:00/km), 10min cooldown.
    """
    return [
        Segment(name="熱身", kind="warmup", distance_m=None, duration_s=900,
                pace_fast_s=345, pace_slow_s=360),
        RepeatGroup(repeat_count=5, steps=[
            Segment(name="閾值4min", kind="training", distance_m=None, duration_s=240,
                    pace_fast_s=255, pace_slow_s=265),
            Segment(name="組間", kind="training", distance_m=None, duration_s=90,
                    pace_fast_s=390, pace_slow_s=420),
        ]),
        Segment(name="收操", kind="cooldown", distance_m=None, duration_s=600,
                pace_fast_s=345, pace_slow_s=360),
    ]


class TestBuildStepsWithRepeatGroup(unittest.TestCase):
    def test_repeat_group_becomes_a_repeat_dict_with_nested_steps(self):
        # This is coros_api.build_run_workout_payload()'s own input contract
        # (checks `"repeat" in step`, reads step["steps"]) -- confirmed by
        # reading that function's source in the installed coros-training-mcp
        # v0.3.1, NOT the is_group/sets/group_id wire/readback shape.
        steps = build_steps(_repeat_group_segments())
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]["kind"], "warmup")
        self.assertEqual(steps[2]["kind"], "cooldown")

        group = steps[1]
        self.assertEqual(group["kind"], "repeat")
        self.assertEqual(group["repeat"], 5)
        self.assertNotIn("is_group", group)
        self.assertNotIn("sets", group)
        self.assertNotIn("group_id", group)

    def test_nested_children_are_built_once_not_multiplied(self):
        # COROS stores/plays a repeat group as one container (sets=N) plus its
        # children listed ONCE -- confirmed against the real readback (Task 7),
        # not physically repeated N times here.
        group = build_steps(_repeat_group_segments())[1]
        self.assertEqual(len(group["steps"]), 2)
        # 2026-09-20 契約變更：重複組內的名稱不再帶配速後綴。COROS 錶上重複組的
        # 子步驟顯示在畫面最下方，`閾值4min (4:15-4:25/km)` 這種長度會被截斷
        # （使用者回報）。配速由 COROS 自己的欄位顯示，塞進名稱是重複資訊。
        self.assertEqual(group["steps"][0]["name"], "閾值4min")
        self.assertEqual(group["steps"][0]["target_duration_seconds"], 240)
        self.assertEqual(group["steps"][1]["target_duration_seconds"], 90)


class TestExpectedDurationSecondsWithRepeatGroup(unittest.TestCase):
    def test_multiplies_group_children_by_repeat_count(self):
        steps = build_steps(_repeat_group_segments())
        # 900 (warmup) + 5*(240+90) (group) + 600 (cooldown) = 3150 -- matches
        # the real workout's own estimated_time_seconds (Task 7 readback).
        self.assertEqual(expected_duration_seconds(steps), 3150.0)


def _real_repeat_group_exercises():
    """De-identified excerpt of the real COROS readback for this workout
    (fetch_scheduled_workouts, 2026-09-02, "獨立強度課 5x4min閾值"). IDs are
    replaced with short placeholders; every other field name/value is kept as
    read back, confirming the is_group/sets/group_id shape from the Task 7
    brief's cygnusb/coros-mcp-derived hypothesis, and that children are listed
    once (not repeated 5 times) alongside their one group container.
    """
    return [
        {
            "id": "1", "name": "熱身", "target_type": 2, "target_value": 900,
            "target_display_unit": 0, "duration_seconds": 900,
            "intensity_type": 3, "intensity_value": 345,
            "intensity_value_extend": 360, "intensity_display_unit": 1,
            "sets": 1, "group_id": "0", "is_group": False,
        },
        {
            "id": "2", "name": "閾值間歇組", "target_type": 2, "target_value": 330,
            "target_display_unit": 0, "duration_seconds": 330,
            "intensity_type": 0, "intensity_value": 0,
            "intensity_value_extend": None, "intensity_display_unit": None,
            "sets": 5, "group_id": "0", "is_group": True,
        },
        {
            "id": "3", "name": "閾值4min", "target_type": 2, "target_value": 240,
            "target_display_unit": 0, "duration_seconds": 240,
            "intensity_type": 3, "intensity_value": 255,
            "intensity_value_extend": 265, "intensity_display_unit": 1,
            "sets": 1, "group_id": "2", "is_group": False,
        },
        {
            "id": "4", "name": "組間", "target_type": 2, "target_value": 90,
            "target_display_unit": 0, "duration_seconds": 90,
            "intensity_type": 3, "intensity_value": 390,
            "intensity_value_extend": 420, "intensity_display_unit": 1,
            "sets": 1, "group_id": "2", "is_group": False,
        },
        {
            "id": "5", "name": "收操", "target_type": 2, "target_value": 600,
            "target_display_unit": 0, "duration_seconds": 600,
            "intensity_type": 3, "intensity_value": 345,
            "intensity_value_extend": 360, "intensity_display_unit": 1,
            "sets": 1, "group_id": "0", "is_group": False,
        },
    ]


def _real_repeat_workout():
    return {
        "estimated_time_seconds": 3150,
        "exercises": [dict(ex) for ex in _real_repeat_group_exercises()],
    }


class TestVerifyWithRepeatGroup(unittest.TestCase):
    def setUp(self):
        self.steps = build_steps(_repeat_group_segments())

    def test_accepts_the_real_workout_shape(self):
        self.assertEqual(verify(_real_repeat_workout(), self.steps), [])

    def test_catches_step_count_mismatch_after_expansion(self):
        workout = _real_repeat_workout()
        workout["exercises"].pop()  # drop the cooldown step
        problems = verify(workout, self.steps)
        self.assertTrue(any("步驟數" in p for p in problems), problems)

    def test_catches_a_silently_collapsed_repeat_count(self):
        # The exact bug category this codebase has repeatedly caught for other
        # parameters (pace preset collapse, miles-vs-km) -- sets=5 requested,
        # server silently stores sets=1.
        workout = _real_repeat_workout()
        for exercise in workout["exercises"]:
            if exercise.get("is_group"):
                exercise["sets"] = 1
        problems = verify(workout, self.steps)
        self.assertTrue(any("重複組" in p and "sets" in p for p in problems), problems)

    def test_catches_pace_collapse_inside_the_group(self):
        workout = _real_repeat_workout()
        for exercise in workout["exercises"]:
            if exercise.get("name") == "閾值4min":
                exercise["intensity_value"] = 390
                exercise["intensity_value_extend"] = 420
        problems = verify(workout, self.steps)
        self.assertTrue(any("intensity_value" in p for p in problems), problems)

    def test_ignores_the_group_container_in_per_step_comparison(self):
        # Regression: the group container itself (is_group=True, no real pace)
        # must not be zipped against an expected child step.
        problems = verify(_real_repeat_workout(), self.steps)
        self.assertEqual(problems, [])

    def test_catches_group_count_mismatch(self):
        workout = _real_repeat_workout()
        # Duplicate the group container so there appear to be two groups.
        group = next(e for e in workout["exercises"] if e.get("is_group"))
        workout["exercises"].append(dict(group, id="6"))
        problems = verify(workout, self.steps)
        self.assertTrue(any("分組數" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()


class TestRepeatGroupStepNamesAreShort(unittest.TestCase):
    """重複組裡的步驟名稱要短——COROS 錶上重複組的子步驟顯示在畫面最下方會被截斷。

    2026-09-20 使用者回報：4×4min 閾值課在錶上「重複組數顯示在最下面顯示不完整」。
    名稱原本是 `{段落名} ({配速})`，例如 `間歇 (4:08-4:15/km)` ＝ 17 個字元。配速在
    COROS 上本來就是獨立欄位會另外顯示，塞進名稱裡是重複資訊，卻正好把名稱撐長到
    被截掉。所以**重複組內的步驟只留段落名**。

    頂層步驟（長跑的熱身/Easy/馬配段/收操）維持原樣——那裡顯示正常，而且名稱帶配速
    在檢視整份課表時有用。`verify()` 只拿名稱當錯誤訊息的標籤、不比對它，所以這個
    改動不影響讀回驗證。
    """

    PLAN = """<!-- coros: day=2026-09-23 name=閾值課 -->

| 段落 | 距離 | 配速 |
|---|---|---|
| 熱身 | 15分鐘 | 5:45-6:00/km |
>>> 重複 4 組
| 間歇 | 4分鐘 | 4:08-4:15/km |
| 組間 | 90秒 | 6:30-7:00/km |
<<<
| 收操 | 10分鐘 | 5:45-6:00/km |
"""

    def _steps(self):
        return build_steps(parse_plan(self.PLAN)[0].segments)

    def test_steps_inside_a_repeat_group_carry_no_pace_suffix(self):
        steps = self._steps()
        group = next(s for s in steps if s.get("kind") == "repeat")
        names = [c["name"] for c in group["steps"]]
        self.assertEqual(["間歇", "組間"], names,
                         "重複組內的名稱要短，配速由 COROS 自己的欄位顯示")
        for n in names:
            self.assertNotIn("/km", n)
            self.assertNotIn("(", n)

    def test_top_level_steps_keep_the_pace_in_the_name(self):
        steps = self._steps()
        top = [s for s in steps if s.get("kind") != "repeat"]
        self.assertTrue(any("5:45-6:00/km" in s["name"] for s in top),
                        "頂層步驟顯示正常，名稱帶配速在檢視整份課表時有用，不要一起改掉")

    def test_repeat_group_children_still_carry_the_right_pace_fields(self):
        """名稱變短不能動到真正決定錶上目標的欄位。"""
        steps = self._steps()
        group = next(s for s in steps if s.get("kind") == "repeat")
        interval = group["steps"][0]
        self.assertEqual(248, interval["intensity_value"])        # 4:08
        self.assertEqual(255, interval["intensity_value_extend"])  # 4:15
        self.assertEqual(240, interval["target_duration_seconds"])
