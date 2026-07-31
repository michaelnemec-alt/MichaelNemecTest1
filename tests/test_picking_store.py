"""Tests for the Prio-vs-Picking on-disk store and its overlay computation.

Covers the round-trip of a stored day, the freeze-once semantics of the
capacity/peak payload (never recomputed unless explicitly forced), and the
pre-pick / same-day overlay counts.

Run with the app venv: `.venv/bin/python -m pytest tests/` or directly:
`.venv/bin/python tests/test_picking_store.py`.
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import picking_store as ps
from views.prio_vs_picking import _compute_overlay, _capacity_kpis


def _with_store(fn):
    with tempfile.TemporaryDirectory() as tmp:
        orig = ps._STORE_DIR
        ps._STORE_DIR = Path(tmp)
        try:
            fn()
        finally:
            ps._STORE_DIR = orig


def _at(day, hour):
    return pd.Timestamp(day.year, day.month, day.day, hour)


def _day_df(day):
    """Two rows finished on `day`, one per AutoStore sector."""
    return pd.DataFrame({
        "AutoStore": ["hu.bud2.91", "hu.bud2.92"],
        "Type": ["STANDARD", "EXPRESS"],
        "Prioritization Time": [_at(day, 10), _at(day, 11)],
        "Finished Picking At": [_at(day, 8), _at(day, 9)],
    })


def test_round_trip():
    def run():
        d = date(2026, 7, 24)
        overlay = {"91": {"prepick": [0] * 24, "sameday": [0] * 24},
                   "92": {"prepick": [0] * 24, "sameday": [0] * 24}}
        ps.save_day("hu.bud2", d, _day_df(d), overlay, plan={10: 5.0}, site="Biatorbágy")

        assert ps.list_warehouses() == ["hu.bud2"]
        assert ps.list_dates("hu.bud2") == [d]

        view = ps.load_day("hu.bud2", d)
        assert view is not None
        assert list(view["df"]["AutoStore"]) == ["hu.bud2.91", "hu.bud2.92"]
        # Derived columns are rebuilt on load.
        for col in ("diff_minutes", "is_next_day", "prio_hour"):
            assert col in view["df"].columns
        assert view["plan"] == {10: 5.0}
        assert view["site"] == "Biatorbágy"
    _with_store(run)


def test_capacity_frozen_then_reused():
    def run():
        d = date(2026, 7, 24)
        overlay = {"91": {"prepick": [0] * 24, "sameday": [0] * 24}}
        cap = {"91": {"maxcap": [7.0] * 24, "bins": [1.0] * 24}}

        ps.save_day("hu.bud2", d, _day_df(d), overlay, capacity=cap, site="X")
        assert ps.has_capacity("hu.bud2", d)

        # A later upload (no capacity) must NOT wipe the frozen value.
        ps.save_day("hu.bud2", d, _day_df(d), overlay, capacity=None,
                    keep_existing_capacity=True)
        view = ps.load_day("hu.bud2", d)
        assert view["capacity"]["91"]["maxcap"] == [7.0] * 24

        # Forcing a recompute (the "recalculate today" button) overwrites it.
        cap2 = {"91": {"maxcap": [9.0] * 24, "bins": [2.0] * 24}}
        ps.save_day("hu.bud2", d, _day_df(d), overlay, capacity=cap2,
                    keep_existing_capacity=False)
        view = ps.load_day("hu.bud2", d)
        assert view["capacity"]["91"]["maxcap"] == [9.0] * 24
    _with_store(run)


def test_compute_overlay_prepick_vs_sameday():
    target = date(2026, 7, 24)
    prev = target - timedelta(days=1)
    rows = pd.DataFrame({
        "AutoStore": ["w.91"] * 3,
        "Type": ["STANDARD"] * 3,
        # prio all on target day at hour 10; one finished the previous day (pre-pick).
        "Prioritization Time": [_at(target, 10)] * 3,
        "Finished Picking At": [
            _at(target, 8),   # same-day
            _at(target, 9),   # same-day
            _at(prev, 20),    # pre-picked previous day
        ],
    })
    rows["prio_hour"] = rows["Prioritization Time"].dt.hour
    ov = _compute_overlay(rows, target)
    assert ov["sameday"][10] == 2
    assert ov["prepick"][10] == 1
    assert sum(ov["sameday"]) == 2 and sum(ov["prepick"]) == 1


def test_capacity_kpis():
    # ceiling known for 2 hours (100+100); picked 60+100 -> lost 40, util 80%.
    theomax = [0.0] * 24
    bins = [0.0] * 24
    theomax[8], theomax[9] = 100.0, 100.0
    bins[8], bins[9] = 60.0, 100.0
    kpi = _capacity_kpis({"theomax": theomax, "bins": bins})
    assert kpi["hours"] == 2
    assert kpi["picked"] == 160.0
    assert kpi["ceiling"] == 200.0
    assert kpi["potential_lost"] == 40.0
    assert round(kpi["utilisation"], 1) == 80.0
    # over-performance in an hour does not create negative lost.
    kpi2 = _capacity_kpis({"theomax": [50.0], "bins": [70.0]})
    assert kpi2["potential_lost"] == 0.0
    # no known ceiling -> None.
    assert _capacity_kpis({"theomax": [0.0, 0.0], "bins": [5.0, 5.0]}) is None
    assert _capacity_kpis(None) is None


def test_missing_day_returns_none():
    def run():
        assert ps.load_day("nope", date(2026, 1, 1)) is None
        assert ps.list_dates("nope") == []
    _with_store(run)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
