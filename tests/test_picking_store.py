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
from views.prio_vs_picking import (
    _compute_overlay, _capacity_kpis, _potential_lost_weekday,
)


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
    # ceiling known for 2 hours; utilisation = avg(grey/purple) over those hours.
    theomax = [0.0] * 24
    total_bins = [0.0] * 24
    bins = [0.0] * 24
    theomax[8], theomax[9] = 100.0, 100.0
    total_bins[8], total_bins[9] = 80.0, 100.0  # grey (all presentations)
    bins[8], bins[9] = 60.0, 100.0              # black (picked cat 1+2)
    kpi = _capacity_kpis(
        {"theomax": theomax, "total_bins": total_bins, "bins": bins})
    assert kpi["hours"] == 2
    # utilisation = mean(80/100, 100/100) = 90%.
    assert round(kpi["utilisation"], 1) == 90.0
    # whole-day cat 1+2 picks = sum of black line.
    assert kpi["picks_cat12"] == 160.0
    # hours without a known ceiling are excluded from the average.
    theomax2 = [0.0] * 24
    total_bins2 = [50.0] * 24
    theomax2[10] = 200.0
    total_bins2[10] = 100.0
    kpi2 = _capacity_kpis(
        {"theomax": theomax2, "total_bins": total_bins2, "bins": [0.0] * 24})
    assert kpi2["hours"] == 1
    assert round(kpi2["utilisation"], 1) == 50.0
    # no known ceiling -> None.
    assert _capacity_kpis(
        {"theomax": [0.0, 0.0], "total_bins": [5.0, 5.0], "bins": [5.0, 5.0]}
    ) is None
    assert _capacity_kpis(None) is None


def test_potential_lost_weekday(monkeypatch):
    import views.prio_vs_picking as pvp

    # target is a Friday; build daily picks over the look-back.
    target = date(2026, 6, 19)  # Friday
    assert target.weekday() == 4
    days = [target - timedelta(days=n) for n in range(0, 70, 7)]  # Fridays
    days += [target - timedelta(days=3)]  # a Tuesday (must be ignored)
    counts = {d: 100.0 for d in days}
    counts[target - timedelta(days=7)] = 500.0  # best Friday
    counts[target] = 300.0                       # today
    counts[target - timedelta(days=3)] = 900.0   # Tuesday, ignored

    def fake_daily(inst_id, start, end):
        rows = [{"date": pd.Timestamp(d), "port_id": 1, "pick_type": "picks",
                 "category": "1", "count": c} for d, c in counts.items()]
        # a non-pick / other-category row that must be filtered out.
        rows.append({"date": pd.Timestamp(target), "port_id": 1,
                     "pick_type": "presentations", "category": "3",
                     "count": 999.0})
        return pd.DataFrame(rows)

    monkeypatch.setattr(pvp, "query_port_wait_time_daily", fake_daily)
    res = _potential_lost_weekday("inst-1", target)
    assert res is not None
    assert res["best"] == 500.0
    assert res["day_picks"] == 300.0
    # lost = 1 - 300/500 = 40%.
    assert round(res["lost_pct"], 1) == 40.0
    assert res["best_date"] == target - timedelta(days=7)

    # no inst id / no data -> None.
    assert _potential_lost_weekday(None, target) is None
    monkeypatch.setattr(pvp, "query_port_wait_time_daily",
                        lambda *a, **k: pd.DataFrame())
    assert _potential_lost_weekday("inst-1", target) is None


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
