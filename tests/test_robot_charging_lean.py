"""Regression test for query_robot_charging_per_robot (the lean sibling of
query_robot_state_per_robot used by the Batteries chart).

Confirms: (1) charging_available + charging_unavailable are summed into a
single charging_s field, and (2) the other 7 state fields (working,
available, recovery, unavailable, service_on_grid, service_off_grid,
battery_pct_avg) are NOT present in the output - that's the whole point of
this function existing separately, so a future edit that accidentally
re-adds them should fail this test.

Run with the app venv: `.venv/bin/python -m pytest tests/` or directly:
`.venv/bin/python tests/test_robot_charging_lean.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cubeanalytics_utils as c

_DROPPED_FIELDS = [
    "working", "available", "recovery", "unavailable",
    "service_on_grid", "service_off_grid", "battery_pct_avg", "total_time_s",
]


def test_sums_both_charging_states_and_drops_the_rest(monkeypatch):
    def fake_fetch_days(url, params):
        return [{
            "date": "2026-08-01",
            "result": {"robot_states": {"00:00:00": [{
                "robot_id": 42, "robot_type": "R5",
                "charging_available": 600, "charging_unavailable": 300,
                "working": 1800, "available": 900, "recovery": 0,
                "unavailable": 0, "service_on_grid": 0, "service_off_grid": 0,
                "battery_pct_avg": 87.5, "total_time_s": 3600,
            }]}},
        }]

    monkeypatch.setattr(c, "_fetch_days", fake_fetch_days)
    df = c.query_robot_charging_per_robot.__wrapped__("INST1", "2026-08-01", "2026-08-01")

    assert len(df) == 1
    row = df.iloc[0]
    assert row["robot_id"] == 42
    assert row["robot_type"] == "R5"
    assert row["charging_s"] == 600 + 300
    for field in _DROPPED_FIELDS:
        assert field not in df.columns, f"{field} should not be carried by the lean parser"


def test_empty_results_returns_empty_dataframe(monkeypatch):
    monkeypatch.setattr(c, "_fetch_days", lambda url, params: [])
    df = c.query_robot_charging_per_robot.__wrapped__("INST1", "2026-08-01", "2026-08-01")
    assert df.empty


if __name__ == "__main__":
    class _MP:
        def setattr(self, obj, name, val):
            setattr(obj, name, val)

    test_sums_both_charging_states_and_drops_the_rest(_MP())
    test_empty_results_returns_empty_dataframe(_MP())
    print("All tests passed!")
