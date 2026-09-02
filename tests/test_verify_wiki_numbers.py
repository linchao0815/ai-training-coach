"""Tests for verify_wiki_numbers.py.

The point of these is the *negative* cases. A checker that only ever passes is
not a defence — the 2026-08-16 audit found three fabricated pace columns that
had sat in the wiki for three weeks precisely because nothing could turn red.
So each test below reintroduces a real historical mistake and asserts the
verifier catches it.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verify_wiki_numbers as vwn


# --------------------------------------------------------------------------
# the real wiki must be clean
# --------------------------------------------------------------------------


def test_current_wiki_has_no_drift():
    problems, used = vwn.run()
    assert problems == [], "\n".join(problems)
    assert used == set(vwn.CHECKERS), "有 checker 沒被任何 wiki 頁面使用"


# --------------------------------------------------------------------------
# cell comparison
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("32.5", 32.5),
        ("32.5 km", 32.5),
        ("**32.5 km**", 32.5),
        ("| 162 min", 162),
        ("5:23/km", "5:23"),
        ("**5:46/km**", "5:46"),
        ("1,358", 1358),
        ("21.6%", 21.6),
    ],
)
def test_cell_matches_accepts_equivalent_formatting(cell, expected):
    assert vwn.cell_matches(cell, expected)


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("5:00/km", "5:23"),      # the 2026 top-6 placeholder
        ("4:00/km", "4:31"),      # the quality-session placeholder
        ("4:48/km", "5:20"),      # the yearly-table placeholder
        ("32.5 km", 31.2),
        ("162 min", 124),
        ("", 30),
        ("n/a", 30),
    ],
)
def test_cell_matches_rejects_wrong_values(cell, expected):
    assert not vwn.cell_matches(cell, expected)


def test_cell_matches_tolerates_rounding_but_not_more():
    assert vwn.cell_matches("22.6 km", 22.65)
    assert not vwn.cell_matches("22.6 km", 22.8)


# --------------------------------------------------------------------------
# table parsing
# --------------------------------------------------------------------------


def test_read_table_extracts_headers_and_rows():
    lines = [
        "<!-- derived: x src=y -->",
        "",
        "| 年 | 次數 |",
        "|---|---:|",
        "| 2021（下半年） | 30 |",
        "| 2022 | 102 |",
        "",
        "後面的段落",
    ]
    headers, rows = vwn.read_table(lines, 1)
    assert headers == ["年", "次數"]
    assert rows == [["2021（下半年）", "30"], ["2022", "102"]]


def test_read_table_refuses_when_prose_sits_between_anchor_and_table():
    lines = ["<!-- derived: x src=y -->", "", "一段說明文字", "", "| 年 |", "|---|"]
    headers, _ = vwn.read_table(lines, 1)
    assert headers is None


# --------------------------------------------------------------------------
# the historical bugs, replayed
# --------------------------------------------------------------------------


def _spec(name):
    return vwn.CHECKERS[name]


def _lines_for(table_markdown):
    return ["<!-- derived: placeholder src=x -->", ""] + table_markdown.strip().splitlines()


def test_catches_the_placeholder_pace_column(tmp_path):
    """The exact bug: every row of the 2026 top-6 table written as 5:00/km."""
    spec = _spec("long_runs_2026_longest")
    result = spec["fn"](vwn.ROOT)
    keys = list(result["rows"])
    body = ["| 日期 | 距離 | 時長 | 配速 | 平均HR | 爬升 |", "|---|---|---|---|---|---|"]
    for key in keys:
        row = result["rows"][key]
        body.append(
            f"| {key} | {row['距離']} km | {row['時長']} min | 5:00/km | {row['平均HR']} | {row['爬升']} m |"
        )
    problems = vwn.check_table(spec, result, str(tmp_path / "p.md"), _lines_for("\n".join(body)), 0)
    assert len(problems) == len(keys)
    assert all("配速" in p and "5:00" in p for p in problems)


def test_catches_a_stale_row_after_resync(tmp_path):
    """Monthly volume left at a half-month number, like 7月 '至07-26 316.8km'."""
    spec = _spec("monthly_2026")
    result = spec["fn"](vwn.ROOT)
    body = ["| 月 | 跑量 | 課次 | 肌力次數 | 負荷總和 |", "|---|---|---|---|---|"]
    for key, row in result["rows"].items():
        volume = 316.8 if key == "2026-07" else row["跑量"]
        body.append(f"| {key} | {volume} km | {row['課次']} | {row['肌力次數']} | {row['負荷總和']} |")
    problems = vwn.check_table(spec, result, str(tmp_path / "p.md"), _lines_for("\n".join(body)), 0)
    assert len(problems) == 1
    assert "2026-07" in problems[0] and "跑量" in problems[0]


def test_catches_a_missing_row(tmp_path):
    spec = _spec("coros_yearly")
    result = spec["fn"](vwn.ROOT)
    body = ["| 年 | 跑量 | 課次 | 肌力次數 | 負荷總和 |", "|---|---|---|---|---|"]
    for key, row in result["rows"].items():
        if key == "2026":
            continue
        body.append(f"| {key} | {row['跑量']} km | {row['課次']} | {row['肌力次數']} | {row['負荷總和']} |")
    problems = vwn.check_table(spec, result, str(tmp_path / "p.md"), _lines_for("\n".join(body)), 0)
    assert any("2026" in p and "缺少" in p for p in problems)


def test_catches_a_renamed_or_dropped_column(tmp_path):
    """Schema drift: someone renames 平均配速 and the checker must not go quiet."""
    spec = _spec("long_runs_by_year")
    result = spec["fn"](vwn.ROOT)
    body = ["| 年 | 次數 | 平均距離 | 最長 | 平均時長 | 配速 | ≥30km | ≥35km |", "|---|---|---|---|---|---|---|---|"]
    for key, row in result["rows"].items():
        body.append(
            f"| {key} | {row['次數']} | {row['平均距離']} km | {row['最長']} km | "
            f"{row['平均時長']} min | {row['平均配速']}/km | {row['≥30km']} | {row['≥35km']} |"
        )
    problems = vwn.check_table(spec, result, str(tmp_path / "p.md"), _lines_for("\n".join(body)), 0)
    assert all("沒有「平均配速」欄" in p for p in problems)
    assert len(problems) == len(result["rows"])


def test_catches_stale_scalar_in_prose(tmp_path):
    """The data-volume snapshot left at the previous sync's counts."""
    spec = _spec("coros_data_volume")
    result = spec["fn"](vwn.ROOT)
    lines = [
        "<!-- derived-scalars: coros_data_volume src=data/coros.db -->",
        "",
        "**目前資料量**：2676 筆活動、1859 天生理紀錄、1844 晚睡眠紀錄。",
    ]
    problems = vwn.check_scalars(spec, result, str(tmp_path / "p.md"), lines, 0)
    assert len(problems) == len(result["values"])


def test_scalar_check_passes_when_every_number_is_present(tmp_path):
    spec = _spec("coros_weekly_spread")
    result = spec["fn"](vwn.ROOT)
    rendered = "、".join(
        f"{label} {value:.1f}" if isinstance(value, float) else f"{label} {value}"
        for label, value in result["values"]
    )
    lines = ["<!-- derived-scalars: coros_weekly_spread src=x -->", "", rendered]
    assert vwn.check_scalars(spec, result, str(tmp_path / "p.md"), lines, 0) == []


# --------------------------------------------------------------------------
# anchor bookkeeping
# --------------------------------------------------------------------------


def test_every_registered_checker_declares_a_source():
    for name, spec in vwn.CHECKERS.items():
        assert spec["src"], f"{name} 沒有標 src"
        assert spec["kind"] in ("table", "scalars")


def test_anchor_regex_reads_both_kinds():
    table = vwn.ANCHOR.search("<!-- derived: monthly_2026 src=data/analysis/monthly_summary.json -->")
    scalars = vwn.ANCHOR.search("<!-- derived-scalars: coros_data_volume src=data/coros.db -->")
    assert table.group("name") == "monthly_2026" and not table.group("scalars")
    assert scalars.group("name") == "coros_data_volume" and scalars.group("scalars")


def test_anchors_inside_code_fences_are_ignored(tmp_path, monkeypatch):
    """WIKI.md documents the anchor syntax inside ``` fences. Those examples are
    documentation, not anchors — the verifier flagged itself on this the first
    time it ran against the finished docs."""
    page = tmp_path / "doc.md"
    page.write_text(
        "# 說明\n\n"
        "```markdown\n"
        "<!-- derived: long_runs_by_year src=data/analysis/long_runs.json -->\n"
        "```\n\n"
        "後面沒有表格。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vwn, "WIKI", str(tmp_path))
    assert list(vwn.find_anchors()) == []


# --------------------------------------------------------------------------
# elapsed-time / explicit-tolerance comparison (added with taipei_race_splits)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("01:31:44", "1:31:44"),
        ("01:31:44", "1:31:50"),          # within the 30s split tolerance
        ("42.46km / 03:10:20", 42.46),    # first number in a compound cell
        ("4:20/km", "4:21"),              # within the 2s pace tolerance
    ],
)
def test_cell_matches_accepts_clock_values(cell, expected):
    assert vwn.cell_matches(cell, expected)


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("01:31:44", "1:35:00"),          # 3+ minutes out
        ("4:20/km", "4:37"),              # a real pace difference, not rounding
        ("沒有時間", "1:31:44"),
    ],
)
def test_cell_matches_rejects_wrong_clock_values(cell, expected):
    assert not vwn.cell_matches(cell, expected)


def test_explicit_tolerance_is_honoured():
    assert vwn.cell_matches("+25.7分", {"value": 25.4, "tol": 0.5})
    assert not vwn.cell_matches("+25.7分", {"value": 22.0, "tol": 0.5})


def test_allow_extra_rows_suppresses_only_the_extra_row_complaint(tmp_path):
    """Partial-coverage escape hatch: rows the checker cannot recompute are
    tolerated, wrong values in rows it can are not."""
    spec = {"name": "fake", "src": "x", "kind": "table"}
    result = {
        "key_re": r"(20\d\d)",
        "rows": {"2024": {"跑量": 3731.5}, "2025": {"跑量": 3430.7}},
        "allow_extra_rows": True,
    }
    good = _lines_for(
        "| 年 | 跑量 |\n|---|---|\n| 2015 | 951.1 |\n| 2024 | 3731.5 |\n| 2025 | 3430.7 |"
    )
    assert vwn.check_table(spec, result, str(tmp_path / "p.md"), good, 0) == []

    bad = _lines_for(
        "| 年 | 跑量 |\n|---|---|\n| 2015 | 951.1 |\n| 2024 | 3731.5 |\n| 2025 | 9999 |"
    )
    problems = vwn.check_table(spec, result, str(tmp_path / "p.md"), bad, 0)
    assert len(problems) == 1 and "2025" in problems[0]

    # without the flag the uncoverable row is itself reported
    result.pop("allow_extra_rows")
    assert any("2015" in p for p in vwn.check_table(spec, result, str(tmp_path / "p.md"), good, 0))


def test_taipei_checker_covers_all_nine_races():
    """Coverage went 5 → 9 on 2026-08-16 by teaching the checker Garmin's lap
    schema. If it ever shrinks, the coverage note on the page must shrink too."""
    result = _spec("taipei_race_splits")["fn"](vwn.ROOT)
    assert set(result["rows"]) == {str(y) for y in range(2017, 2026)}
    assert "allow_extra_rows" not in result


def test_garmin_race_splits_needs_the_eight_hour_correction():
    """Garmin stores a naive local time as if it were UTC. Without +8h the
    2019-12-15 race is looked up on the wrong date and nothing is found."""
    assert vwn._garmin_race_splits("2019-12-15") is not None
    assert vwn._garmin_race_splits("2019-12-14") is None


def test_garmin_distance_comes_from_laps_not_the_summary_column():
    """activities.distance_meters is often the rounded 42.2km; the wiki quotes
    the lap-summed GPS distance (43.53km for 2019)."""
    splits = vwn._garmin_race_splits("2019-12-15")
    assert abs(splits["手錶距離/時間"] - 43.53) < 0.02
