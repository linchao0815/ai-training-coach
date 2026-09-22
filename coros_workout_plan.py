"""
Pure helpers for turning a training-plan markdown doc into COROS run-workout
steps, and for verifying what COROS stored afterwards.

Standard library only, and deliberately no `coros_api` import: this module must
be importable by a plain `python3` so the tests can run without the
coros-training-mcp venv. All network work lives in create_workouts.py.
"""

import re
from dataclasses import dataclass

# COROS display-unit codes. coros_api's own defaults are inconsistent between
# these two fields — see _resolve_run_target() and build_run_workout_payload() —
# target_display_unit defaults to 3 (miles) while intensity_display_unit defaults
# to 0, so every step we build must set both explicitly rather than assume either
# default is km. Getting target_display_unit wrong renders every distance in
# miles on the watch while the workout title still says km (happened 2026-08-02).
DISPLAY_UNIT_KM = 1

# A bare "4:45/km" is widened by this many seconds on each side.
SINGLE_PACE_TOLERANCE_S = 5

_BOLD_RE = re.compile(r"\*\*")
_PARENTHETICAL_RE = re.compile(r"[（(][^）)]*[）)]")
_DISTANCE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*km$", re.IGNORECASE)
_PACE_RE = re.compile(
    r"^(\d{1,2}):([0-5]\d)(?:\s*-\s*(\d{1,2}):([0-5]\d))?\s*/\s*km$", re.IGNORECASE
)
_MINUTES_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*分鐘$")
_SECONDS_RE = re.compile(r"^(\d+)\s*秒$")


def clean_cell(text):
    """Strip markdown bold and any parenthetical note from a table cell."""
    without_bold = _BOLD_RE.sub("", text)
    without_notes = _PARENTHETICAL_RE.sub("", without_bold)
    return without_notes.strip()


def parse_distance(text):
    """'8km' -> 8000 (metres). Cumulative ranges like '0-8km' are rejected."""
    match = _DISTANCE_RE.match(text.strip())
    if not match:
        raise ValueError(
            f"無法解析距離 {text!r}。需要分段長度如 '8km'，不是累積區間如 '0-8km'。"
        )
    return int(round(float(match.group(1)) * 1000))


def parse_pace(text):
    """'5:40-5:55/km' -> (340, 355) seconds per km, (faster, slower)."""
    match = _PACE_RE.match(text.strip())
    if not match:
        raise ValueError(
            f"無法解析配速 {text!r}。需要 '5:40-5:55/km' 或 '4:45/km'。"
        )
    fast = int(match.group(1)) * 60 + int(match.group(2))
    if match.group(3) is None:
        return fast - SINGLE_PACE_TOLERANCE_S, fast + SINGLE_PACE_TOLERANCE_S
    slow = int(match.group(3)) * 60 + int(match.group(4))
    if slow <= fast:
        raise ValueError(f"配速區間 {text!r} 的後值必須比前值慢。")
    return fast, slow


def parse_duration(text):
    """'15分鐘' -> 900, '90秒' -> 90 (秒)。"""
    stripped = text.strip()
    minutes_match = _MINUTES_RE.match(stripped)
    if minutes_match:
        return int(round(float(minutes_match.group(1)) * 60))
    seconds_match = _SECONDS_RE.match(stripped)
    if seconds_match:
        return int(seconds_match.group(1))
    raise ValueError(
        f"無法解析時間長度 {text!r}。需要 'N分鐘' 或 'N秒'。"
    )


def parse_distance_or_duration(text):
    """'8km' -> ('distance', 8000)；'15分鐘'/'90秒' -> ('duration', seconds)。"""
    stripped = text.strip()
    if _DISTANCE_RE.match(stripped):
        return "distance", parse_distance(stripped)
    if _MINUTES_RE.match(stripped) or _SECONDS_RE.match(stripped):
        return "duration", parse_duration(stripped)
    raise ValueError(
        f"無法解析距離或時間長度 {text!r}。需要 'Nkm'、'N分鐘' 或 'N秒'。"
    )


_ANCHOR_RE = re.compile(r"<!--\s*coros:\s*day=(\d{4})-(\d{2})-(\d{2})\s+name=(.+?)\s*-->")
_REPEAT_OPEN_RE = re.compile(r"^>>>\s*重複\s*(\d+)\s*組\s*$")
_REPEAT_CLOSE_RE = re.compile(r"^<<<\s*$")

# 段落名稱含這些字串時，覆蓋依位置推斷的 kind。
_KIND_BY_NAME = (("熱身", "warmup"), ("收操", "cooldown"))


@dataclass
class Segment:
    name: str
    kind: str
    distance_m: int | None
    duration_s: int | None
    pace_fast_s: int
    pace_slow_s: int


@dataclass
class RepeatGroup:
    repeat_count: int
    steps: list


@dataclass
class PlanEntry:
    day: str
    name: str
    segments: list


def _split_row(line):
    """'| a | b |' -> ['a', 'b']"""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line):
    return set(line.strip().strip("|").replace(":", "").replace("-", "").replace("|", "").strip()) == set()


def _infer_kind(name, position, total):
    for needle, kind in _KIND_BY_NAME:
        if needle in name:
            return kind
    if position == 0:
        return "warmup"
    if position == total - 1 and total >= 3:
        return "cooldown"
    return "training"


def _row_to_segment(cells, name_col, distance_col, pace_col, kind):
    name = clean_cell(cells[name_col])
    kind_of_value, value = parse_distance_or_duration(clean_cell(cells[distance_col]))
    pace_fast, pace_slow = parse_pace(clean_cell(cells[pace_col]))
    return Segment(
        name=name,
        kind=kind,  # None 代表交給呼叫端事後用 _infer_kind() 決定
        distance_m=value if kind_of_value == "distance" else None,
        duration_s=value if kind_of_value == "duration" else None,
        pace_fast_s=pace_fast,
        pace_slow_s=pace_slow,
    )


def _parse_repeat_block(lines, start, day, name_col, distance_col, pace_col):
    """lines[start] 是 '>>> 重複 N 組' 那一行。回傳 (RepeatGroup, 下一個要處理的行號)。"""
    open_match = _REPEAT_OPEN_RE.match(lines[start].strip())
    repeat_count = int(open_match.group(1))
    index = start + 1
    steps = []
    while index < len(lines) and not _REPEAT_CLOSE_RE.match(lines[index].strip()):
        line = lines[index]
        if line.lstrip().startswith("|") and not _is_separator(line):
            cells = _split_row(line)
            if max(name_col, distance_col, pace_col) >= len(cells):
                raise ValueError(f"{day} 的重複區塊第 {index - start} 列欄位不足：{cells}")
            steps.append(_row_to_segment(cells, name_col, distance_col, pace_col, "training"))
        index += 1
    if index >= len(lines):
        raise ValueError(f"{day} 的重複區塊缺少結尾 '<<<'。")
    if not steps:
        raise ValueError(f"{day} 的重複區塊裡沒有任何課表列。")
    return RepeatGroup(repeat_count=repeat_count, steps=steps), index + 1


def _table_header_columns(header_line):
    header = _split_row(header_line)

    def column_index(label):
        for i, cell in enumerate(header):
            if label in cell:
                return i
        return None

    return header, column_index("配速"), column_index("距離")


def _collect_entry_segments(lines, start, day):
    """從錨點下一行開始，收集到下一個錨點或檔尾為止的所有片段（表格 + 重複區塊）。"""
    segments = []
    index = start
    header_cols = None  # (pace_col, distance_col)，由遇到的第一張表格表頭決定

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _ANCHOR_RE.search(line):
            break

        if stripped.startswith("#"):
            break

        if _REPEAT_OPEN_RE.match(stripped):
            if header_cols is None:
                raise ValueError(f"{day} 的重複區塊出現在任何表格之前，不知道欄位順序。")
            pace_col, distance_col = header_cols
            group, index = _parse_repeat_block(lines, index, day, 0, distance_col, pace_col)
            segments.append(group)
            continue

        if line.lstrip().startswith("|"):
            table = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table.append(lines[index])
                index += 1
            if header_cols is None:
                # 這堂課遇到的第一張表格：table[0] 是表頭，決定欄位順序。
                header, pace_col, distance_col = _table_header_columns(table[0])
                if pace_col is None:
                    raise ValueError(f"{day} 的分段表缺少「配速」欄，實際表頭：{header}")
                if distance_col is None:
                    raise ValueError(f"{day} 的分段表缺少「距離」欄，實際表頭：{header}")
                header_cols = (pace_col, distance_col)
                body = [row for row in table[1:] if not _is_separator(row)]
            else:
                # 重複區塊之後接續的表格片段：沒有自己的表頭，
                # 整段都是資料列，沿用同一堂課第一張表格決定的欄位順序。
                pace_col, distance_col = header_cols
                body = [row for row in table if not _is_separator(row)]
            for row in body:
                cells = _split_row(row)
                if max(distance_col, pace_col) >= len(cells):
                    raise ValueError(f"{day} 的分段表有一列欄位不足：{cells}")
                segments.append(_row_to_segment(cells, 0, distance_col, pace_col, kind=None))
            continue

        index += 1

    if not segments:
        raise ValueError(f"{day} 的錨點後面找不到表格或課表內容。")

    total = len(segments)
    for position, segment in enumerate(segments):
        if isinstance(segment, Segment) and segment.kind is None:
            segment.kind = _infer_kind(segment.name, position, total)

    return segments, index


def parse_plan(text):
    """Extract every `<!-- coros: ... -->` anchored workout from a plan doc."""
    lines = text.splitlines()
    entries = []
    index = 0
    while index < len(lines):
        match = _ANCHOR_RE.search(lines[index])
        if not match:
            index += 1
            continue
        year, month, day_of_month, name = match.groups()
        day = f"{year}{month}{day_of_month}"
        segments, index = _collect_entry_segments(lines, index + 1, day)
        entries.append(PlanEntry(day=day, name=name.strip(), segments=segments))
    if not entries:
        raise ValueError("文件中找不到 `<!-- coros: day=... name=... -->` 錨點。")
    return entries


def _format_pace(fast_s, slow_s):
    return f"{fast_s // 60}:{fast_s % 60:02d}-{slow_s // 60}:{slow_s % 60:02d}/km"


def build_steps(segments, short_names=False):
    """Segments -> coros_api run steps, with both display units pinned to km.

    Deliberately does NOT emit the `pace` / `intensity_label` fields: that path
    goes through coros-training-mcp's pace_parser, which silently collapses every
    step to the `Pace` preset defaults (186411/223694 ms/km ≈ 3:06-3:44/km).

    Duration-based steps use "target_type": "time" — confirmed against this
    project's own incident history (doc/coros-estimated-time-bug.md line 64,
    wiki/concepts/coros-unit-pitfall.md: "時間型步驟（target_type=time）不受影響"),
    not a guess. The seconds-value field name (`target_duration_seconds`) is
    confirmed correct for THIS function's output by reading coros_api's own
    `_resolve_run_target()` source (installed coros-training-mcp v0.3.1): it
    looks up exactly `step["target_duration_seconds"]` for time-typed steps.
    Note this is the OUTGOING (send) field name only — the READBACK from
    fetch_workout()/fetch_scheduled_workouts() names the equivalent field
    `duration_seconds` instead (confirmed against a real account 2026-09-02,
    Task 7); `verify()` below uses that different name deliberately, it is not
    a typo.

    RepeatGroup segments become `{"kind": "repeat", "repeat": N, "steps": [...]}`.
    This shape is NOT the cygnusb/coros-mcp `is_group`/`sets`/`group_id` wire
    shape from the Task 7 brief's hypothesis — that hypothesis described the
    READBACK shape (confirmed correct below, in `verify()`), but this function
    builds the OUTGOING `steps` argument that create_workouts.py hands straight
    to `coros_api.create_run_workout(auth, name, steps)`. Reading that
    function's real source (`build_run_workout_payload()` in coros_api.py)
    shows it dispatches on `"repeat" in step`, taking `step["repeat"]` as the
    count and `step["steps"]` as the (unmultiplied, single-iteration) child
    step list, then converts that itself into the is_group/sets/group_id wire
    shape when building the POST payload. So this function must speak
    coros_api's `repeat`/`steps` input contract, not the wire shape directly.
    """
    steps = []
    for segment in segments:
        if isinstance(segment, RepeatGroup):
            steps.append({
                "kind": "repeat",
                "repeat": segment.repeat_count,
                # 重複組的子步驟在 COROS 錶上顯示在畫面最下方，名稱太長會被截斷
                # （2026-09-20 使用者回報 4×4min 閾值課「重複組數顯示不完整」）。
                # 配速在 COROS 本來就是獨立欄位會另外顯示，塞進名稱是重複資訊，
                # 卻正好把 `間歇` 撐成 `間歇 (4:08-4:15/km)` 共 17 個字元。
                # 所以組內只留段落名；頂層步驟維持帶配速（那裡顯示正常）。
                "steps": build_steps(segment.steps, short_names=True),
            })
            continue
        step = {
            "kind": segment.kind,
            "name": segment.name if short_names else
                    f"{segment.name} ({_format_pace(segment.pace_fast_s, segment.pace_slow_s)})",
            "intensity_type": 3,
            "is_intensity_percent": False,
            "intensity_value": segment.pace_fast_s,
            "intensity_value_extend": segment.pace_slow_s,
            "intensity_display_unit": DISPLAY_UNIT_KM,
        }
        if segment.distance_m is not None:
            step["target_type"] = "distance"
            step["target_distance_meters"] = segment.distance_m
            step["target_display_unit"] = DISPLAY_UNIT_KM
        else:
            step["target_type"] = "time"
            step["target_duration_seconds"] = segment.duration_s
        steps.append(step)
    return steps


# estimated_time_seconds 與自算值的容許誤差比例。
ESTIMATED_TIME_TOLERANCE = 0.2


def expected_duration_seconds(steps):
    """Duration implied by distance x mid-range pace (or the duration itself), in seconds.

    A repeat-group step (`{"kind": "repeat", "repeat": N, "steps": [...]}`, see
    build_steps()) contributes N times its single-iteration children's total —
    the children list holds only one iteration's worth of steps (matching what
    actually gets sent/stored), so the multiplier has to be applied here.
    """
    total = 0.0
    for step in steps:
        if step.get("kind") == "repeat":
            total += expected_duration_seconds(step["steps"]) * step["repeat"]
            continue
        mid_pace = (step["intensity_value"] + step["intensity_value_extend"]) / 2.0
        if step["target_type"] == "distance":
            km = step["target_distance_meters"] / 1000.0
            total += km * mid_pace
        else:
            total += step["target_duration_seconds"]
    return total


def _flatten_expected_steps(steps):
    """Expand repeat-group entries into their child steps, for comparison against
    a real COROS readback.

    Confirmed against a real account (2026-09-02, 5x4min threshold workout,
    Task 7): a repeat group is stored as ONE is_group=True container (carrying
    the repeat count in `sets`) plus its child exercises listed ONCE each — not
    physically repeated `sets` times. So the expected per-child comparison
    below must also see each child once; the repeat counts pulled out here are
    checked separately against the real is_group containers.

    Returns (flat_child_steps, expected_repeat_counts) — the latter in the same
    order the repeat groups appear, for zipping against the real workout's
    is_group=True exercises.
    """
    flat = []
    repeat_counts = []
    for step in steps:
        if step.get("kind") == "repeat":
            repeat_counts.append(step["repeat"])
            flat.extend(step["steps"])
        else:
            flat.append(step)
    return flat, repeat_counts


def verify(workout, expected_steps):
    """Check a workout read back from COROS. Empty list means it is safe to schedule.

    `expected_steps` is build_steps()'s output and may contain repeat-group
    entries (`{"kind": "repeat", "repeat": N, "steps": [...]}`). Those are
    flattened via `_flatten_expected_steps()` before the per-step field
    comparison below, since the real readback lists each child once (not N
    times) — see that function's docstring. The is_group=True group container
    itself is excluded from `exercises` (as before: `not e.get("is_group")`)
    but is separately checked here against the expected repeat count via its
    `sets` field (confirmed real field name, Task 7).
    """
    problems = []
    all_actual = [e for e in (workout.get("exercises") or []) if isinstance(e, dict)]
    exercises = [e for e in all_actual if not e.get("is_group")]
    actual_groups = [e for e in all_actual if e.get("is_group")]

    # Keep the original (possibly nested) `expected_steps` around for
    # expected_duration_seconds() below — that function needs the repeat
    # multiplier, which the flattened, single-iteration `flat_expected` below
    # deliberately drops (see _flatten_expected_steps()'s docstring).
    flat_expected, expected_repeat_counts = _flatten_expected_steps(expected_steps)

    if len(expected_repeat_counts) != len(actual_groups):
        problems.append(
            f"分組數不符：預期 {len(expected_repeat_counts)} 組，實際 {len(actual_groups)} 組"
        )
    else:
        for position, (expected_count, group) in enumerate(
            zip(expected_repeat_counts, actual_groups), 1
        ):
            if group.get("sets") != expected_count:
                problems.append(
                    f"第 {position} 個重複組 sets 不符：預期 {expected_count}，"
                    f"實際 {group.get('sets')}"
                )

    if len(exercises) != len(flat_expected):
        problems.append(
            f"步驟數不符：預期 {len(flat_expected)}，實際 {len(exercises)}"
        )

    for position, (exercise, expected) in enumerate(zip(exercises, flat_expected), 1):
        label = f"第 {position} 步（{expected['name']}）"
        for field in ("intensity_value", "intensity_value_extend"):
            if exercise.get(field) != expected[field]:
                problems.append(
                    f"{label} {field} 不符：預期 {expected[field]}（秒/km），"
                    f"實際 {exercise.get(field)}"
                )
        if exercise.get("intensity_display_unit") != DISPLAY_UNIT_KM:
            problems.append(
                f"{label} intensity_display_unit 應為 {DISPLAY_UNIT_KM}，"
                f"實際 {exercise.get('intensity_display_unit')}"
            )
        if expected["target_type"] == "distance":
            if exercise.get("target_display_unit") != DISPLAY_UNIT_KM:
                problems.append(
                    f"{label} target_display_unit 應為 {DISPLAY_UNIT_KM}（公里），"
                    f"實際 {exercise.get('target_display_unit')} —— 距離會顯示成英里"
                )
            expected_target_value = expected["target_distance_meters"] * 100
            if exercise.get("target_value") != expected_target_value:
                problems.append(
                    f"{label} target_value 不符：預期 {expected_target_value}（公分），"
                    f"實際 {exercise.get('target_value')}"
                )
        else:
            # Readback field is `duration_seconds`, NOT `target_duration_seconds`
            # (that name is only correct for the outgoing build_steps() field
            # coros_api._resolve_run_target() consumes — see build_steps()'s
            # docstring). Confirmed against a real account 2026-09-02 (Task 7).
            expected_duration = expected["target_duration_seconds"]
            if exercise.get("duration_seconds") != expected_duration:
                problems.append(
                    f"{label} duration_seconds 不符：預期 {expected_duration}（秒），"
                    f"實際 {exercise.get('duration_seconds')}"
                )

    expected_pairs = {
        (s["intensity_value"], s["intensity_value_extend"]) for s in flat_expected
    }
    actual_pairs = {
        (e.get("intensity_value"), e.get("intensity_value_extend")) for e in exercises
    }
    if len(expected_pairs) > 1 and len(actual_pairs) == 1:
        problems.append(
            "所有步驟配速相同，但計畫指定了不同配速 —— "
            "這是 coros-training-mcp `pace` 參數塌成 preset 預設值的指紋"
        )

    estimated = workout.get("estimated_time_seconds")
    calculated = expected_duration_seconds(expected_steps)
    if estimated is None:
        problems.append("estimated_time_seconds 缺值")
    elif calculated and abs(estimated - calculated) > calculated * ESTIMATED_TIME_TOLERANCE:
        problems.append(
            f"estimated_time_seconds 不合理：{estimated}，"
            f"自算約 {calculated:.0f} 秒（差距超過 {ESTIMATED_TIME_TOLERANCE:.0%}）"
        )

    return problems
