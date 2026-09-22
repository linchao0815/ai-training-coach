"""
Pure helpers for building Garmin Connect strength-training workout payloads.

Garmin's own `garminconnect[workout]` typed models (RunningWorkout,
CyclingWorkout, ...) have no strength-training equivalent -- there is no
exercise/sets/reps schema in that package at all (confirmed by reading
garminconnect/workout.py: SportType only lists RUNNING/CYCLING/SWIMMING/
WALKING/HIKING/FITNESS_EQUIPMENT/MULTI_SPORT/OTHER). So this module builds
the raw JSON payload by hand instead of going through pydantic models.

The schema below was reverse-engineered by reading back a real strength
workout the user created in the Garmin Connect app (workoutId 1706486120,
2026-09-22) via `client.get_workout_by_id()`, cross-checked against the
community reference https://github.com/n1t3k/garmin-strength-api. It is
POSTed via `garminconnect.Garmin.upload_workout()`, which accepts a raw
dict/list and skips the typed-model layer entirely -- no `garminconnect`
import needed here either, so (like garmin_workout_plan.py) this module
stays importable without the `[workout]` extra.

Known gotcha (confirmed against a real account): an `exerciseName` that
isn't one of Garmin's ~1000 catalogued names is silently accepted and
stored empty -- it does NOT error. `category` must be a valid one of
Garmin's ~33 categories or the API returns 400. So an unconfirmed
`exerciseName` degrades gracefully (blank exercise label, everything else
-- category, reps/time, weight, rest -- still correct); an unconfirmed
`category` breaks the whole upload. Pick approximations accordingly.
"""

SPORT_TYPE = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}

_STEP_TYPE_INTERVAL = {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3}
_STEP_TYPE_REST = {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5}
_STEP_TYPE_REPEAT = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}

_COND_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
_COND_REPS = {"conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True}
# conditionTypeId 7 ("iterations") is for the RepeatGroup's own set count, NOT
# a per-exercise rep count -- easy to mix up with conditionTypeId 10 ("reps").
_COND_ITERATIONS = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}

_NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}

_KG_UNIT = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


class Exercise:
    """One movement. `target` is `"time"` (value = seconds held) or
    `"reps"` (value = rep count). `weight_kg` is optional."""

    def __init__(self, category, exercise_name, target, value, rest_s=0, weight_kg=None):
        assert target in ("time", "reps"), target
        self.category = category
        self.exercise_name = exercise_name
        self.target = target
        self.value = value
        self.rest_s = rest_s
        self.weight_kg = weight_kg


class Circuit:
    """A group of `exercises` performed back-to-back, repeated `iterations`
    times as one round-robin circuit (Garmin's RepeatGroupDTO with >1
    exercise inside). Use `Exercise` directly (not wrapped in a Circuit)
    for a movement done as its own single set -- `build_steps()` wraps it
    in an iterations=1 RepeatGroupDTO automatically, matching the shape
    Garmin's own workout builder produces even for a single set (see
    real captured example: every exercise sits inside its own
    RepeatGroupDTO, not as a bare top-level step)."""

    def __init__(self, iterations, exercises):
        self.iterations = iterations
        self.exercises = exercises


class _Order:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


def _exercise_step(order, exercise):
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order.next(),
        "stepType": _STEP_TYPE_INTERVAL,
        "endCondition": _COND_TIME if exercise.target == "time" else _COND_REPS,
        "endConditionValue": float(exercise.value),
        "targetType": _NO_TARGET,
        "category": exercise.category,
        "exerciseName": exercise.exercise_name,
    }
    if exercise.weight_kg:
        step["weightValue"] = float(exercise.weight_kg)
        step["weightUnit"] = _KG_UNIT
    return step


def _rest_step(order, seconds):
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order.next(),
        "stepType": _STEP_TYPE_REST,
        "endCondition": _COND_TIME,
        "endConditionValue": float(seconds),
        "targetType": _NO_TARGET,
    }


def _repeat_group(order, iterations, exercises):
    group_order = order.next()
    children = []
    for exercise in exercises:
        children.append(_exercise_step(order, exercise))
        if exercise.rest_s:
            children.append(_rest_step(order, exercise.rest_s))
    return {
        "type": "RepeatGroupDTO",
        "stepOrder": group_order,
        "stepType": _STEP_TYPE_REPEAT,
        "numberOfIterations": iterations,
        "endCondition": _COND_ITERATIONS,
        "endConditionValue": float(iterations),
        # Without this the app inserts a trailing rest after the very last
        # set with nothing left to recover for.
        "skipLastRestStep": True,
        "smartRepeat": False,
        "workoutSteps": children,
    }


def build_steps(items):
    """`items`: list of `Exercise` (each becomes its own iterations=1
    RepeatGroupDTO) and/or `Circuit` (becomes one RepeatGroupDTO with
    iterations>1 wrapping all its exercises). Returns the flat, ordered
    list of top-level RepeatGroupDTO step dicts."""
    order = _Order()
    steps = []
    for item in items:
        if isinstance(item, Circuit):
            steps.append(_repeat_group(order, item.iterations, item.exercises))
        else:
            steps.append(_repeat_group(order, 1, [item]))
    return steps


def build_workout(name, items):
    """`items`: see `build_steps()`. Returns the full payload for
    `garminconnect.Garmin.upload_workout()`."""
    return {
        "sportType": SPORT_TYPE,
        "workoutName": name,
        "workoutSegments": [
            {"segmentOrder": 1, "sportType": SPORT_TYPE, "workoutSteps": build_steps(items)}
        ],
    }


def _flatten_intended(items):
    """`items` -> flat list of (category, exercise_name, target, value,
    weight_kg) tuples in upload order, expanding Circuit iterations."""
    flat = []
    for item in items:
        exercises = item.exercises if isinstance(item, Circuit) else [item]
        repeats = item.iterations if isinstance(item, Circuit) else 1
        for _ in range(repeats):
            for exercise in exercises:
                flat.append((
                    exercise.category, exercise.exercise_name,
                    exercise.target, float(exercise.value), exercise.weight_kg,
                ))
    return flat


def _flatten_readback(workout_steps):
    """Garmin's stored `workoutSteps` (RepeatGroupDTO list) -> same shape as
    `_flatten_intended()`, expanding each group's `numberOfIterations`."""
    flat = []
    for group in workout_steps:
        iterations = group.get("numberOfIterations", 1)
        exercise_steps = [
            s for s in group.get("workoutSteps", [])
            if s.get("stepType", {}).get("stepTypeKey") == "interval"
        ]
        for _ in range(iterations):
            for step in exercise_steps:
                target = "time" if step["endCondition"]["conditionTypeKey"] == "time" else "reps"
                flat.append((
                    step.get("category"), step.get("exerciseName"),
                    target, step["endConditionValue"],
                    step.get("weightValue"),
                ))
    return flat


def verify(readback, items):
    """Compare what Garmin actually stored (`get_workout_by_id()`'s return
    value) against the `items` that were sent.

    Returns `(problems, warnings)`: `problems` empty means safe to
    schedule. `exerciseName` mismatches go into `warnings`, not
    `problems` — confirmed against a real account that Garmin silently
    blanks an `exerciseName` it doesn't recognise rather than rejecting
    the upload, so this field alone diverging is expected for
    approximated exercises (see module docstring), not a sign the
    request was corrupted.
    """
    problems = []
    warnings = []
    segments = readback.get("workoutSegments") or []
    if not segments:
        return ["回傳沒有 workoutSegments"], warnings
    stored = _flatten_readback(segments[0].get("workoutSteps") or [])
    intended = _flatten_intended(items)

    if len(stored) != len(intended):
        problems.append(f"步驟數不符：預期 {len(intended)}，實際 {len(stored)}")
        return problems, warnings  # per-index comparison would be misleading once counts differ

    for i, ((exp_cat, exp_name, exp_target, exp_value, exp_weight),
            (got_cat, got_name, got_target, got_value, got_weight)) in enumerate(zip(intended, stored)):
        if exp_cat != got_cat:
            problems.append(f"[{i}] category 不符：預期 {exp_cat!r}，實際 {got_cat!r}")
        if exp_target != got_target:
            problems.append(f"[{i}] target 型別不符：預期 {exp_target!r}，實際 {got_target!r}")
        if exp_value != got_value:
            problems.append(f"[{i}] 數值不符：預期 {exp_value!r}，實際 {got_value!r}")
        if exp_weight and got_weight and abs(exp_weight - got_weight) > 0.01:
            problems.append(f"[{i}] 重量不符：預期 {exp_weight!r}kg，實際 {got_weight!r}kg")
        if got_name != exp_name:
            warnings.append(
                f"[{i}] exerciseName 被 Garmin 存成 {got_name!r}（預期 {exp_name!r}）"
                "——動作字典查無此名，不是上傳出錯"
            )
    return problems, warnings
