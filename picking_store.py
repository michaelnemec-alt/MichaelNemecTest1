"""Persistent on-disk store for Prio-vs-Picking uploads.

Uploaded picking CSVs are ingested once and kept on the Streamlit disk-cache
volume (survives container restart / rebuild), so history can be browsed
instantly without re-uploading and — crucially — without recomputing the
CubeAnalytics peak-bin-presentation envelope.

The peak envelope (and the per-day capacity arrays derived from the
CubeAnalytics API) is *frozen* at ingest time: each day keeps the value it had
when it was first stored, so a later upload with a shifted look-back window
never rewrites history. Picking rows and the pre-pick overlay come straight
from the uploaded CSV and are re-stored on every upload (newest wins).

Layout:
    <store>/<warehouse>/<YYYY-MM-DD>.parquet   base picking rows finished that day
    <store>/<warehouse>/<YYYY-MM-DD>.json      overlay + frozen capacity + meta
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_STORE_DIR = Path(
    os.environ.get("PICKING_STORE_DIR", str(Path.home() / ".streamlit" / "picking_store"))
)

_BASE_COLS = ["AutoStore", "Type", "Prioritization Time", "Finished Picking At"]
# Extra columns kept when present so the delayed-orders table can show when an
# order was submitted to AutoStore and how that lines up with its prio time.
_EXTRA_COLS = ["Order ID", "Submitted At", "Started Picking At", "Port"]
_DT_COLS = ("Prioritization Time", "Finished Picking At",
            "Submitted At", "Started Picking At")


def _wh_dir(warehouse):
    return _STORE_DIR / warehouse


def _parquet_path(warehouse, day):
    return _wh_dir(warehouse) / f"{day.isoformat()}.parquet"


def _json_path(warehouse, day):
    return _wh_dir(warehouse) / f"{day.isoformat()}.json"


def list_warehouses():
    """Warehouses that have at least one stored day, sorted."""
    if not _STORE_DIR.exists():
        return []
    out = []
    for d in _STORE_DIR.iterdir():
        if d.is_dir() and any(d.glob("*.parquet")):
            out.append(d.name)
    return sorted(out)


def list_dates(warehouse):
    """Stored dates for a warehouse, sorted ascending (as datetime.date)."""
    wh = _wh_dir(warehouse)
    if not wh.exists():
        return []
    dates = []
    for p in wh.glob("*.parquet"):
        try:
            dates.append(datetime.strptime(p.stem, "%Y-%m-%d").date())
        except ValueError:
            continue
    return sorted(dates)


def has_day(warehouse, day):
    return _parquet_path(warehouse, day).exists()


def _read_meta(warehouse, day):
    path = _json_path(warehouse, day)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def has_capacity(warehouse, day):
    """True if a frozen capacity payload already exists for the day."""
    return bool(_read_meta(warehouse, day).get("capacity"))


def stored_columns(warehouse, day):
    """Column names in the stored parquet for a day (empty list if absent).

    Reads only the parquet schema, not the row data, so it is cheap to call
    while deciding whether a day already on disk needs re-storing.
    """
    ppath = _parquet_path(warehouse, day)
    if not ppath.exists():
        return []
    try:
        import pyarrow.parquet as pq
        return list(pq.ParquetFile(ppath).schema.names)
    except Exception:
        try:
            return list(pd.read_parquet(ppath).columns)
        except Exception:
            return []


def has_submit_col(warehouse, day):
    """True if the stored day already carries the AutoStore submit timestamp.

    Days stored before the delayed-orders table existed lack ``Submitted At``;
    re-uploading such a day should refresh it rather than skip it.
    """
    return "Submitted At" in stored_columns(warehouse, day)


def save_day(warehouse, day, df_day, overlay, capacity=None, plan=None,
             source=None, site=None, keep_existing_capacity=True):
    """Persist one day's picking rows + overlay, freezing capacity on first write.

    df_day     : DataFrame of rows finished on `day` (at least _BASE_COLS).
    overlay    : {"91": {"prepick": [24], "sameday": [24]}, "92": {...}}.
    capacity   : {"91": {...arrays...}, "92": {...}} or None (API-derived).
    plan       : {hour: planned_orders} or None.
    keep_existing_capacity : if True and a frozen capacity already exists, keep
                             it (never recompute history); pass False to force
                             overwrite (e.g. the "recalculate today" button).
    """
    wh = _wh_dir(warehouse)
    wh.mkdir(parents=True, exist_ok=True)

    cols = [c for c in (_BASE_COLS + _EXTRA_COLS) if c in df_day.columns]
    out = df_day[cols].copy()
    for c in _DT_COLS:
        if c in out.columns:
            out[c] = pd.to_datetime(out[c], errors="coerce")
    out.to_parquet(_parquet_path(warehouse, day), index=False)

    existing = _read_meta(warehouse, day)
    if capacity is None and keep_existing_capacity:
        capacity = existing.get("capacity")
    if site is None:
        site = existing.get("site")

    meta = {
        "warehouse": warehouse,
        "date": day.isoformat(),
        "source": source,
        "site": site,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "overlay": overlay,
        "plan": {str(k): v for k, v in plan.items()} if plan else None,
        "capacity": capacity,
    }
    with open(_json_path(warehouse, day), "w", encoding="utf-8") as f:
        json.dump(meta, f)


def load_day(warehouse, day):
    """Load a stored day into a view dict, or None if absent.

    Returns {"warehouse", "date", "df", "overlay", "capacity", "plan"} where
    `df` is the picking rows with the same derived columns the live path uses.
    """
    ppath = _parquet_path(warehouse, day)
    if not ppath.exists():
        return None
    df = pd.read_parquet(ppath)
    for c in _DT_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    df = df.dropna(subset=["Prioritization Time", "Finished Picking At"])
    df["diff_minutes"] = (
        (df["Prioritization Time"] - df["Finished Picking At"]).dt.total_seconds() / 60
    )
    df["is_next_day"] = df["Prioritization Time"].dt.date > df["Finished Picking At"].dt.date
    df["prio_hour"] = df["Prioritization Time"].dt.hour

    meta = _read_meta(warehouse, day)
    plan = meta.get("plan")
    plan = {int(k): v for k, v in plan.items()} if plan else None
    return {
        "warehouse": warehouse,
        "date": day,
        "df": df,
        "overlay": meta.get("overlay") or {},
        "capacity": meta.get("capacity"),
        "plan": plan,
        "site": meta.get("site"),
    }
