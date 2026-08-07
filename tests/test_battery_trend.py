"""Unit tests for the battery degradation-trend projection logic
(_project_replace_by, _monthly_battery_trend) in views/cube_analytics.py.

These are pure-pandas/numpy functions with no Streamlit or network
dependency, so they're tested directly without mocking _fetch_days.

Run with the app venv: `.venv/bin/python -m pytest tests/` or directly:
`.venv/bin/python tests/test_battery_trend.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from views.cube_analytics import _project_replace_by, _monthly_battery_trend, _DEGRADATION_THRESHOLD_DEFAULT


def _months(scores):
    return pd.DataFrame({
        "month": pd.date_range("2026-01-01", periods=len(scores), freq="MS"),
        "score_smoothed": scores,
    })


def test_declining_robot_projects_a_future_crossing():
    g = _months([4.0, 3.6, 3.2, 2.8])  # declining ~0.4/month, threshold 2.5
    slope, months_to, confidence = _project_replace_by(g)
    assert slope < 0
    assert months_to is not None
    assert 0 < months_to < 2  # close, since already near threshold
    assert confidence == "ok"  # 4 points


def test_already_below_threshold_returns_zero_months():
    g = _months([2.4, 2.2, 2.0])
    slope, months_to, confidence = _project_replace_by(g)
    assert months_to == 0
    assert confidence == "low"  # only 3 points


def test_stable_or_improving_robot_has_no_projection():
    g = _months([4.0, 4.1, 4.0, 4.2])
    slope, months_to, confidence = _project_replace_by(g)
    assert slope >= 0
    assert months_to is None


def test_single_data_point_is_low_confidence_no_projection():
    g = _months([3.0])
    slope, months_to, confidence = _project_replace_by(g)
    assert months_to is None
    assert confidence == "low"


def test_monthly_trend_aggregates_and_smooths(monkeypatch):
    import cubeanalytics_utils as c

    def fake_fetch_days(url, params):
        # Two days in Jan, two in Feb, same robot - Jan avg should be (4+2)/2=3,
        # Feb avg (2+2)/2=2, and the 3-month rolling average (window=2 points
        # available) should differ from the raw monthly average.
        return [
            {"date": "2026-01-01", "result": {"robots": [{"robot_id": 1, "capacity_estimation_score": 4}]}},
            {"date": "2026-01-02", "result": {"robots": [{"robot_id": 1, "capacity_estimation_score": 2}]}},
            {"date": "2026-02-01", "result": {"robots": [{"robot_id": 1, "capacity_estimation_score": 2}]}},
            {"date": "2026-02-02", "result": {"robots": [{"robot_id": 1, "capacity_estimation_score": 2}]}},
        ]

    monkeypatch.setattr(c, "_fetch_days", fake_fetch_days)
    monthly = _monthly_battery_trend("INST1", "2026-01-01", "2026-02-28")
    assert len(monthly) == 2
    jan_row = monthly[monthly["month"] == "2026-01-01"].iloc[0]
    feb_row = monthly[monthly["month"] == "2026-02-01"].iloc[0]
    assert jan_row["capacity_estimation_score"] == 3.0
    assert feb_row["capacity_estimation_score"] == 2.0
    # 3-month rolling with only 2 points available: (3.0 + 2.0) / 2 = 2.5
    assert feb_row["score_smoothed"] == 2.5


if __name__ == "__main__":
    test_declining_robot_projects_a_future_crossing()
    test_already_below_threshold_returns_zero_months()
    test_stable_or_improving_robot_has_no_projection()
    test_single_data_point_is_low_confidence_no_projection()

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)
    test_monthly_trend_aggregates_and_smooths(_MP())
    print("All tests passed!")
