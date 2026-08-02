"""Tests for _live_events merging the collector store with the REST window.

Proves that when the collector store is mounted the live views serve the full
retained history from disk and merge the REST ~48h window on top, de-duplicated
by uuid; and that without the store they fall back to REST only. The network
primitive (_fetch_live_events) is stubbed — no live API is used.

Run with the app venv: `.venv/bin/python -m pytest tests/`.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cubeanalytics_utils as c


def _bt(uid, ts):
    return {
        "installation_id": "INST1", "event_type": "BIN_AND_TASK",
        "uuid": uid, "date": ts[:10], "local_installation_timestamp": ts,
        "data": {"total": 1},
    }


def _write_store(root, inst, day, events):
    d = os.path.join(root, inst, "BIN_AND_TASK")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{day}.ndjson"), "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def test_merges_store_history_with_rest_and_dedups(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        # Store holds two old days the REST 48h window no longer serves, plus
        # one event ("c") that overlaps the REST window.
        _write_store(root, "INST1", "2020-01-01",
                     [_bt("a", "2020-01-01T00:00:00+00:00")])
        _write_store(root, "INST1", "2020-01-02",
                     [_bt("b", "2020-01-02T00:00:00+00:00"),
                      _bt("c", "2020-01-02T00:05:00+00:00")])
        os.environ["RAW_EVENTS_DIR"] = root
        try:
            # REST returns the recent tail: the overlapping "c" plus a fresh "d".
            rest = [_bt("c", "2020-01-02T00:05:00+00:00"),
                    _bt("d", "2020-01-02T00:10:00+00:00")]
            monkeypatch.setattr(c, "_fetch_live_events",
                                lambda inst, hrs, et: list(rest))

            out = c._live_events("INST1", 48, "BIN_AND_TASK")
            uids = sorted(e["uuid"] for e in out)
            # Full history (a, b) + overlap (c, once) + fresh REST (d).
            assert uids == ["a", "b", "c", "d"]
        finally:
            del os.environ["RAW_EVENTS_DIR"]


def test_falls_back_to_rest_without_store(monkeypatch):
    os.environ.pop("RAW_EVENTS_DIR", None)
    rest = [_bt("x", "2020-01-02T00:00:00+00:00")]
    monkeypatch.setattr(c, "_fetch_live_events", lambda inst, hrs, et: list(rest))
    out = c._live_events("INST1", 48, "BIN_AND_TASK")
    assert [e["uuid"] for e in out] == ["x"]


def test_rest_error_still_returns_store_history(monkeypatch):
    with tempfile.TemporaryDirectory() as root:
        _write_store(root, "INST1", "2020-01-01",
                     [_bt("a", "2020-01-01T00:00:00+00:00")])
        os.environ["RAW_EVENTS_DIR"] = root
        try:
            def _boom(inst, hrs, et):
                raise c.requests.RequestException("down")

            monkeypatch.setattr(c, "_fetch_live_events", _boom)
            out = c._live_events("INST1", 48, "BIN_AND_TASK")
            assert [e["uuid"] for e in out] == ["a"]
        finally:
            del os.environ["RAW_EVENTS_DIR"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
