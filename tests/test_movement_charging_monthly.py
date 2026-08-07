"""Tests for query_movement_charging_monthly and the per-month aggregate
cache it's built on (_iter_months, _month_bounds, clear_month_cache).

The core property under test: a CLOSED (fully-past) calendar month is
computed once and then never re-fetched or re-aggregated again, even across
a second call - that's the whole point of this cache existing (it's what
lets the original distance-vs-charging-time metric be built without
repeating the OOM-causing full-range /robot-state/ pull on every page load).

Run with the app venv: `.venv/bin/python -m pytest tests/` or directly:
`.venv/bin/python tests/test_movement_charging_monthly.py`.
"""
import os
import shutil
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import cubeanalytics_utils as c


def test_iter_months_spans_calendar_months_inclusive():
    months = c._iter_months("2026-01-15", "2026-03-05")
    assert months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_iter_months_single_month():
    months = c._iter_months("2026-06-01", "2026-06-30")
    assert months == [date(2026, 6, 1)]


def test_month_bounds_clips_to_overall_range():
    start, end = c._month_bounds(date(2026, 3, 1), date(2026, 3, 10), date(2026, 3, 20))
    assert start == date(2026, 3, 10)
    assert end == date(2026, 3, 20)


def test_month_bounds_full_month_when_range_covers_it():
    start, end = c._month_bounds(date(2026, 4, 1), date(2026, 1, 1), date(2026, 12, 31))
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 30)


def test_closed_month_is_never_refetched_across_two_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "_MONTH_CACHE_DIR", tmp_path)

    call_count = {"movement": 0, "charging": 0}

    def fake_movement(inst_id, after, before):
        call_count["movement"] += 1
        return pd.DataFrame({"robot_id": [1, 2], "distance_km": [10.0, 20.0]})

    def fake_charging(inst_id, after, before):
        call_count["charging"] += 1
        return pd.DataFrame({"robot_id": [1, 2], "charging_s": [600, 1200]})

    monkeypatch.setattr(c, "query_robot_movement", fake_movement)
    monkeypatch.setattr(c, "query_robot_charging_per_robot", fake_charging)

    # A month range that is unambiguously in the past relative to "today" in
    # this test environment - both January and February 2020 are closed.
    df1 = c.query_movement_charging_monthly("INST1", "2020-01-01", "2020-02-28")
    assert call_count["movement"] == 2  # Jan + Feb, first time
    assert call_count["charging"] == 2
    assert len(df1) == 4  # 2 robots x 2 months

    # Second call, same closed range: the underlying fetchers must NOT be
    # called again - both months should be served from the on-disk aggregate
    # cache written during the first call.
    df2 = c.query_movement_charging_monthly("INST1", "2020-01-01", "2020-02-28")
    assert call_count["movement"] == 2  # unchanged
    assert call_count["charging"] == 2  # unchanged
    pd.testing.assert_frame_equal(
        df1.sort_values(["robot_id", "month"]).reset_index(drop=True),
        df2.sort_values(["robot_id", "month"]).reset_index(drop=True),
    )


def test_clear_month_cache_forces_refetch(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "_MONTH_CACHE_DIR", tmp_path)
    call_count = {"n": 0}

    def fake_movement(inst_id, after, before):
        call_count["n"] += 1
        return pd.DataFrame({"robot_id": [1], "distance_km": [5.0]})

    monkeypatch.setattr(c, "query_robot_movement", fake_movement)
    monkeypatch.setattr(c, "query_robot_charging_per_robot", lambda *a: pd.DataFrame())

    c.query_movement_charging_monthly("INST1", "2020-01-01", "2020-01-31")
    assert call_count["n"] == 1

    c.query_movement_charging_monthly("INST1", "2020-01-01", "2020-01-31")
    assert call_count["n"] == 1  # cached, no refetch

    removed = c.clear_month_cache("INST1")
    assert removed >= 1

    c.query_movement_charging_monthly("INST1", "2020-01-01", "2020-01-31")
    assert call_count["n"] == 2  # cache cleared, refetched


if __name__ == "__main__":
    test_iter_months_spans_calendar_months_inclusive()
    test_iter_months_single_month()
    test_month_bounds_clips_to_overall_range()
    test_month_bounds_full_month_when_range_covers_it()

    class _MP:
        def __init__(self):
            self._patches = []

        def setattr(self, obj, name, val):
            self._patches.append((obj, name, getattr(obj, name, None)))
            setattr(obj, name, val)

    tmp = tempfile.mkdtemp()
    try:
        mp = _MP()
        test_closed_month_is_never_refetched_across_two_calls(mp, __import__("pathlib").Path(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("All tests passed!")
