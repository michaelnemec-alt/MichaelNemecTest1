"""Tests for the collector's append-only raw NDJSON store.

Covers the on-disk layout, uuid deduplication (within a run and across a
restart), and that events missing placement fields are rejected.

Run with the app venv: `.venv/bin/python -m pytest tests/` or directly:
`.venv/bin/python tests/test_raw_store.py`.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collector.raw_store import RawStore


def _event(uuid, inst="INST1", event_type="ROBOT_STATE", date_str="2026-07-30"):
    return {
        "installation_id": inst,
        "event_type": event_type,
        "date": date_str,
        "uuid": uuid,
        "data": {"x": 1},
    }


def test_layout_and_write():
    with tempfile.TemporaryDirectory() as root:
        store = RawStore(root)
        assert store.write(_event("a")) is True
        store.close()
        path = os.path.join(root, "INST1", "ROBOT_STATE", "2026-07-30.ndjson")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        assert len(lines) == 1 and lines[0]["uuid"] == "a"


def test_dedup_within_run():
    with tempfile.TemporaryDirectory() as root:
        store = RawStore(root)
        assert store.write(_event("a")) is True
        assert store.write(_event("a")) is False
        assert store.write(_event("b")) is True
        store.close()
        path = os.path.join(root, "INST1", "ROBOT_STATE", "2026-07-30.ndjson")
        with open(path, encoding="utf-8") as fh:
            assert sum(1 for l in fh if l.strip()) == 2


def test_dedup_across_restart():
    with tempfile.TemporaryDirectory() as root:
        store = RawStore(root)
        store.write(_event("a"))
        store.close()
        store2 = RawStore(root)
        assert store2.write(_event("a")) is False
        assert store2.write(_event("c")) is True
        store2.close()
        path = os.path.join(root, "INST1", "ROBOT_STATE", "2026-07-30.ndjson")
        with open(path, encoding="utf-8") as fh:
            uuids = [json.loads(l)["uuid"] for l in fh if l.strip()]
        assert uuids == ["a", "c"]


def test_rejects_incomplete_events():
    with tempfile.TemporaryDirectory() as root:
        store = RawStore(root)
        assert store.write({"event_type": "X", "date": "2026-07-30"}) is False
        assert store.write("not-a-dict") is False
        store.close()


def test_splits_by_type_and_day():
    with tempfile.TemporaryDirectory() as root:
        store = RawStore(root)
        store.write(_event("a", event_type="ROBOT_STATE", date_str="2026-07-30"))
        store.write(_event("b", event_type="CHARGER_STATE", date_str="2026-07-30"))
        store.write(_event("c", event_type="ROBOT_STATE", date_str="2026-07-31"))
        store.close()
        assert os.path.exists(os.path.join(root, "INST1", "ROBOT_STATE", "2026-07-30.ndjson"))
        assert os.path.exists(os.path.join(root, "INST1", "CHARGER_STATE", "2026-07-30.ndjson"))
        assert os.path.exists(os.path.join(root, "INST1", "ROBOT_STATE", "2026-07-31.ndjson"))


if __name__ == "__main__":
    test_layout_and_write()
    test_dedup_within_run()
    test_dedup_across_restart()
    test_rejects_incomplete_events()
    test_splits_by_type_and_day()
    print("ok")
