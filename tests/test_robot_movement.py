"""Regression test for /robot-movement parsing.

The endpoint returns two API versions (2.0.0 current, 1.3.0 deprecated) for
the same day/robot; only 2.0.0 must be counted or distance/lifts double up.

Run with the app venv: `.venv/bin/python -m pytest tests/` or directly:
`.venv/bin/python tests/test_robot_movement.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cubeanalytics_utils as c


def test_only_2_0_0_version_counted_no_double_count(monkeypatch):
    def fake_fetch_days(url, params):
        return [
            {
                "date": "2026-08-01",
                "version": "1.3.0",
                "result": {"robot_movements": {"1": {
                    "distance_x_m": 999.0, "distance_y_m": 0, "distance_z_m": 0, "total_lifts": 999,
                }}},
            },
            {
                "date": "2026-08-01",
                "version": "2.0.0",
                "result": {"robot_movements": {"1": {
                    "distance_x_m": 100.0, "distance_y_m": 200.0, "distance_z_m": 300.0, "total_lifts": 50,
                }}},
            },
        ]

    monkeypatch.setattr(c, "_fetch_days", fake_fetch_days)
    df = c.query_robot_movement.__wrapped__("INST1", "2026-08-01", "2026-08-01")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["robot_id"] == 1  # cast to int, not the "1" string key
    assert row["distance_km"] == (100.0 + 200.0 + 300.0) / 1000.0
    assert row["total_lifts"] == 50


def test_empty_results_returns_empty_dataframe(monkeypatch):
    monkeypatch.setattr(c, "_fetch_days", lambda url, params: [])
    df = c.query_robot_movement.__wrapped__("INST1", "2026-08-01", "2026-08-01")
    assert df.empty


if __name__ == "__main__":
    import pandas as pd  # noqa: F401 - ensures pandas import errors surface early

    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    test_only_2_0_0_version_counted_no_double_count(_MP())
    test_empty_results_returns_empty_dataframe(_MP())
    print("All tests passed!")
