"""Read raw collector events (NDJSON) for the app.

The night collector writes every WebSocket/REST event verbatim to
``<root>/<installation_id>/<EVENT_TYPE>/<YYYY-MM-DD>.ndjson`` (see
``collector/raw_store.py``). Some event types — notably CHARGER_STATE and
STATUS — are WebSocket-only and never served by the REST live-events-stream, so
the collector store is the *only* source for them. This module reads that store
read-only for the live page.

The store root is taken from ``RAW_EVENTS_DIR`` (the app mounts the collector's
data volume there, read-only). When it is unset or absent — e.g. local dev with
no collector — the readers return empty frames so the page can explain that no
collected data is available yet.
"""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd


def raw_events_dir():
    return os.environ.get("RAW_EVENTS_DIR", "").strip()


def is_available():
    d = raw_events_dir()
    return bool(d) and Path(d).is_dir()


def _iter_event_lines(installation_id, event_type, days):
    """Yield parsed events for the given installation/type over the last `days`
    calendar days (by the file's date partition), oldest file first."""
    root = raw_events_dir()
    if not root:
        return
    base = Path(root) / str(installation_id) / event_type
    if not base.is_dir():
        return
    wanted = {(date.today() - timedelta(days=i)).isoformat() for i in range(days)}
    for day_str in sorted(wanted):
        path = base / f"{day_str}.ndjson"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def read_stream_events(installation_id, event_type):
    """Yield every stored raw event for one installation/type across all day
    files in the collector store — the full retained history, independent of
    the box clock (unlike ``_iter_event_lines`` which windows by ``date.today``).
    Yields nothing when the store is not mounted or has no data for the type."""
    root = raw_events_dir()
    if not root:
        return
    base = Path(root) / str(installation_id) / event_type
    if not base.is_dir():
        return
    for path in sorted(base.glob("*.ndjson")):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


_CHARGER_STATES = ("on", "off", "charging", "error")


def read_charger_state(installation_id, last_hours=48):
    """Per 5-min charger snapshots for one installation from the collector store.

    Returns a long DataFrame with one row per (timestamp, charger):
      ts, charger_id, charger_type, state, temp_max (max non-null connector/
      battery temperature for that charger, else NaN), and the four
      state_time_span_seconds columns (on/off/charging/error, summing to ~300).
    Empty when the collector has no CHARGER_STATE for this site.
    """
    days = max(1, (int(last_hours) + 23) // 24 + 1)
    temp_fields = (
        "battery_internal_temp_max", "battery_connector_temp_max",
        "robot_connector_initial_temp", "robot_connector_temp_max",
        "charger_connector_temp_max",
    )
    rows = []
    for e in _iter_event_lines(installation_id, "CHARGER_STATE", days):
        ts = e.get("local_installation_timestamp")
        for ch in e.get("data", {}).get("chargers", []):
            spans = ch.get("state_time_span_seconds", {}) or {}
            # All temperatures come as tenths of a degree Celsius (per the
            # CubeAnalytics schema), so scale by 1/10.
            temp_cols = {}
            temp_vals = []
            for f in temp_fields:
                v = ch.get(f)
                scaled = float(v) / 10.0 if v is not None else float("nan")
                temp_cols[f] = scaled
                if v is not None:
                    temp_vals.append(scaled)
            rows.append({
                "ts": ts,
                "charger_id": ch.get("charger_id"),
                "charger_type": ch.get("charger_type", ""),
                "state": ch.get("state", ""),
                "temp_max": max(temp_vals) if temp_vals else float("nan"),
                **temp_cols,
                **{s: float(spans.get(s, 0) or 0) for s in _CHARGER_STATES},
            })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Keep the installation's local wall-clock: local_installation_timestamp
    # carries the site's UTC offset (e.g. +02:00), so strip the trailing offset
    # instead of converting to UTC (which would shift the whole day by the
    # offset and mislabel evening snapshots as morning).
    naive = df["ts"].astype(str).str.replace(
        r"(Z|[+-]\d{2}:?\d{2})$", "", regex=True)
    df["ts"] = pd.to_datetime(naive, errors="coerce")
    df = df.dropna(subset=["ts"])
    cutoff = datetime.now() - timedelta(hours=int(last_hours))
    df = df[df["ts"] >= cutoff]
    return df.sort_values(["ts", "charger_id"]).reset_index(drop=True)


def charger_state_concurrent(df):
    """Collapse the long charger frame to avg concurrent chargers per state per
    5-min: for each timestamp, sum each charger's state seconds / 300. Also
    carries chargers (count) and temp_max (max across chargers) per timestamp."""
    if df is None or df.empty:
        return pd.DataFrame()
    g = df.groupby("ts")
    out = (g[list(_CHARGER_STATES)].sum() / 300.0)
    out["chargers"] = g["charger_id"].nunique()
    out["temp_max"] = g["temp_max"].max()
    return out.reset_index()
