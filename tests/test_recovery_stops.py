"""Regression tests for uptime stop classification and midnight stitching.

Run with the app venv: `.venv/bin/python -m pytest tests/` or, since pytest is
not a runtime dependency, directly: `.venv/bin/python tests/test_recovery_stops.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cubeanalytics_utils import _recovery_rows_from_uptime


def _page(*periods):
    return {"result": {"periods": list(periods)}}


def _seg(code, start, end, down):
    return {
        "mode": "downtime",
        "stop_code_name": code,
        "start_at": start,
        "end_at": end,
        "down_seconds": down,
    }


def test_non_manual_codes_count_as_errors():
    rows = _recovery_rows_from_uptime([_page(
        _seg("XHANDLER_ROBOT_ERROR_FAILED", "2026-06-01 08:00:00", "2026-06-01 08:05:00", 300),
        _seg("RETRANS_MISSING_AP", "2026-06-02 09:00:00", "2026-06-02 09:03:00", 180),
        _seg("ROBOT_DOOR_STOP", "2026-06-03 10:00:00", "2026-06-03 10:01:00", 60),
        _seg("STOPPED_FROM_CONSOLE", "2026-06-04 11:00:00", "2026-06-04 11:02:00", 120),
    )])
    cats = [r["category"] for r in rows]
    assert cats.count("error_stop") == 3
    assert cats.count("manual") == 1


def test_midnight_split_is_stitched_into_one_stop():
    # One physical RETRANS stop split by the daily reporting boundary.
    rows = _recovery_rows_from_uptime([_page(
        _seg("RETRANS_MISSING_AP", "2026-05-02 19:31:00", "2026-05-02 23:59:59", 16104),
        _seg("RETRANS_MISSING_AP", "2026-05-03 00:00:00", "2026-05-03 12:34:55", 45295),
    )])
    assert len(rows) == 1
    assert rows[0]["category"] == "error_stop"
    assert rows[0]["recovery_seconds"] == 16104 + 45295


def test_distinct_stops_with_real_gap_are_not_merged():
    rows = _recovery_rows_from_uptime([_page(
        _seg("STOPPED_FROM_CONSOLE", "2026-06-01 08:00:00", "2026-06-01 08:02:00", 120),
        _seg("STOPPED_FROM_CONSOLE", "2026-06-01 09:00:00", "2026-06-01 09:01:00", 60),
    )])
    assert len(rows) == 2


def test_empty_and_uncoded_segments_ignored():
    rows = _recovery_rows_from_uptime([_page(
        {"mode": "uptime", "up_seconds": 3600},
        _seg(None, "2026-06-01 08:00:00", "2026-06-01 08:05:00", 300),
    )])
    assert rows == []


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
