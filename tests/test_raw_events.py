"""Tests for the app-side reader of the collector's raw NDJSON store.

Covers CHARGER_STATE flattening (one row per timestamp x charger), the
last-hours cutoff, temperature extraction, and the concurrent-per-state
collapse used by the Live data page.

Run with the app venv: `.venv/bin/python -m pytest tests/`.
"""
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import raw_events


def _charger(cid, state, spans, temp=None):
    ch = {
        "charger_id": cid,
        "state": state,
        "charger_type": "R5/R5+ Charge Point",
        "battery_internal_temp_max": None,
        "battery_connector_temp_max": None,
        "robot_connector_initial_temp": None,
        "robot_connector_temp_max": temp,
        "charger_connector_temp_max": None,
        "state_time_span_seconds": spans,
    }
    return ch


def _event(inst, ts, chargers, day_str):
    return {
        "installation_id": inst,
        "event_type": "CHARGER_STATE",
        "uuid": f"{inst}-{ts}",
        "date": day_str,
        "local_installation_timestamp": ts,
        "data": {"chargers": chargers},
    }


def _write(root, inst, day_str, events):
    d = os.path.join(root, inst, "CHARGER_STATE")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{day_str}.ndjson"), "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")


def test_read_and_concurrent():
    with tempfile.TemporaryDirectory() as root:
        os.environ["RAW_EVENTS_DIR"] = root
        try:
            inst = "INSTX"
            now = datetime.now(timezone.utc)
            today = date.today().isoformat()
            ts = now.replace(microsecond=0).isoformat()
            evs = [_event(inst, ts, [
                # temp arrives as tenths of a degree (410 -> 41.0 C)
                _charger(1, "charging",
                         {"on": 0, "off": 0, "charging": 300, "error": 0}, temp=410),
                _charger(2, "on",
                         {"on": 300, "off": 0, "charging": 0, "error": 0}),
            ], today)]
            _write(root, inst, today, evs)

            df = raw_events.read_charger_state(inst, 48)
            assert len(df) == 2
            assert set(df["charger_id"]) == {1, 2}
            # temp extracted from the one non-null connector field, scaled /10
            assert df.loc[df["charger_id"] == 1, "temp_max"].iloc[0] == 41.0

            conc = raw_events.charger_state_concurrent(df)
            assert len(conc) == 1
            row = conc.iloc[0]
            assert abs(row["charging"] - 1.0) < 1e-9
            assert abs(row["on"] - 1.0) < 1e-9
            assert int(row["chargers"]) == 2
            assert row["temp_max"] == 41.0
        finally:
            del os.environ["RAW_EVENTS_DIR"]


def test_last_hours_cutoff_and_missing_dir():
    with tempfile.TemporaryDirectory() as root:
        os.environ["RAW_EVENTS_DIR"] = root
        try:
            inst = "INSTY"
            old = datetime.now(timezone.utc) - timedelta(hours=40)
            day_str = old.date().isoformat()
            evs = [_event(inst, old.replace(microsecond=0).isoformat(), [
                _charger(1, "on", {"on": 300, "off": 0, "charging": 0, "error": 0}),
            ], day_str)]
            _write(root, inst, day_str, evs)
            # 40h-old event is dropped by a 6h window...
            assert raw_events.read_charger_state(inst, 6).empty
            # ...but kept by a 48h window (file for that date is within range).
            assert not raw_events.read_charger_state(inst, 48).empty
        finally:
            del os.environ["RAW_EVENTS_DIR"]

    # No RAW_EVENTS_DIR -> unavailable and empty, never raises.
    os.environ.pop("RAW_EVENTS_DIR", None)
    assert raw_events.is_available() is False
    assert raw_events.read_charger_state("Z", 48).empty


def test_read_stream_events_spans_all_day_files():
    with tempfile.TemporaryDirectory() as root:
        os.environ["RAW_EVENTS_DIR"] = root
        try:
            inst = "INSTZ"

            def bt(day, ts, uid):
                return {
                    "installation_id": inst, "event_type": "BIN_AND_TASK",
                    "uuid": uid, "date": day,
                    "local_installation_timestamp": ts,
                    "data": {"total": 1},
                }

            d = os.path.join(root, inst, "BIN_AND_TASK")
            os.makedirs(d, exist_ok=True)
            # Two day files, well outside any date.today() window, to prove the
            # reader is clock-independent (unlike read_charger_state).
            with open(os.path.join(d, "2020-01-01.ndjson"), "w") as fh:
                fh.write(json.dumps(bt("2020-01-01", "2020-01-01T00:00:00+00:00", "a")) + "\n")
            with open(os.path.join(d, "2020-01-02.ndjson"), "w") as fh:
                fh.write(json.dumps(bt("2020-01-02", "2020-01-02T00:00:00+00:00", "b")) + "\n")

            events = list(raw_events.read_stream_events(inst, "BIN_AND_TASK"))
            assert [e["uuid"] for e in events] == ["a", "b"]
            # Unknown installation / missing store -> empty, never raises.
            assert list(raw_events.read_stream_events("NOPE", "BIN_AND_TASK")) == []
        finally:
            del os.environ["RAW_EVENTS_DIR"]


if __name__ == "__main__":
    test_read_and_concurrent()
    test_last_hours_cutoff_and_missing_dir()
    test_read_stream_events_spans_all_day_files()
    print("ok")
