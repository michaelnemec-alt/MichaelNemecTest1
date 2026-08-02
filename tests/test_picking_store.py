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
    _compute_overlay, _capacity_kpis, _weekday_kpi_matrix, _avail_robot_hours,
    _ingest_upload,
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


def test_ingest_skips_days_already_stored():
    """Re-uploading a file whose days are all already stored must not reprocess
    them: store_dates is empty, every candidate day is reported as skipped, and
    the oldest day is still dropped (incomplete pre-pick)."""
    def run():
        d0, d1, d2 = date(2026, 7, 23), date(2026, 7, 24), date(2026, 7, 25)
        overlay = {"91": {"prepick": [0] * 24, "sameday": [0] * 24}}
        # d1 and d2 already on disk; d0 is only in the file as the oldest day.
        ps.save_day("hu.bud2", d1, _day_df(d1), overlay, site="X")
        ps.save_day("hu.bud2", d2, _day_df(d2), overlay, site="X")

        df = pd.concat([_day_df(d0), _day_df(d1), _day_df(d2)], ignore_index=True)
        store_dates, dropped, skipped = _ingest_upload(
            df, "hu.bud2", {}, None, show_capacity=False,
            plan_by_date={}, source="f.csv",
        )
        assert store_dates == []
        assert dropped == d0
        assert skipped == [d1, d2]
    _with_store(run)


def test_ingest_stores_only_new_days(monkeypatch):
    """A file overlapping the store plus a new day only processes the new day."""
    def run():
        d0, d1, d2 = date(2026, 7, 23), date(2026, 7, 24), date(2026, 7, 25)
        overlay = {"91": {"prepick": [0] * 24, "sameday": [0] * 24}}
        ps.save_day("hu.bud2", d1, _day_df(d1), overlay, site="X")  # already stored

        df = pd.concat([_day_df(d0), _day_df(d1), _day_df(d2)], ignore_index=True)
        df["prio_hour"] = df["Prioritization Time"].dt.hour
        store_dates, dropped, skipped = _ingest_upload(
            df, "hu.bud2", {}, None, show_capacity=False,
            plan_by_date={}, source="f.csv",
        )
        assert store_dates == [d2]  # only the new day
        assert skipped == [d1]
        assert dropped == d0
        assert ps.list_dates("hu.bud2") == [d1, d2]
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
    # pick yield = cat 1+2 picks / all bin presentations across the whole day.
    assert kpi["total_presentations"] == 180.0
    assert round(kpi["pick_yield"], 4) == round(160.0 / 180.0 * 100.0, 4)
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


def test_avail_robot_hours():
    import pandas as pd
    from datetime import date

    # 2 hours of robot-state; robot-hours = Σ(total_s − charging_unavailable_s)/3600.
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-06-01", "2026-06-01", "2026-06-02"]),
        "hour": [8, 9, 8],
        "total_s": [360000.0, 360000.0, 360000.0],  # 100 robots * 3600 s
        "charging_unavailable_s": [36000.0, 72000.0, 0.0],
    })
    # day 2026-06-01: (360000-36000)/3600 + (360000-72000)/3600 = 90 + 80 = 170.
    assert round(_avail_robot_hours(df, date(2026, 6, 1)), 1) == 170.0
    # a day with no rows -> NaN.
    assert _avail_robot_hours(df, date(2026, 5, 30)) != _avail_robot_hours(
        df, date(2026, 5, 30))
    # empty / missing frame -> NaN.
    assert _avail_robot_hours(pd.DataFrame(), date(2026, 6, 1)) != \
        _avail_robot_hours(pd.DataFrame(), date(2026, 6, 1))


def test_site_aliases():
    from cubeanalytics_utils import site_alias, site_display_label

    # the two Pragues must resolve differently.
    assert site_alias("Praha") == "PRG2 / Prague 2"
    assert site_alias("Chrášťany u Prahy") == "PRG3 / Prague 3"
    assert site_alias("01937-Rohlik-Bischofsheim (Ambient)") == "FRA / Frankfurt"
    assert site_alias("Garching") == "MUC / Munich"
    assert site_alias("Nowhere") is None
    assert site_display_label("Praha") == "Praha · PRG2 / Prague 2"
    assert site_display_label("Nowhere") == "Nowhere"


def test_weekday_kpi_matrix(monkeypatch):
    import views.prio_vs_picking as pvp

    target = date(2026, 6, 19)  # Friday
    assert target.weekday() == 4
    fridays = [target - timedelta(days=n) for n in range(0, 70, 7)]
    counts = {d: 100.0 for d in fridays}
    counts[target - timedelta(days=7)] = 500.0  # best Friday
    counts[target] = 300.0
    counts[target - timedelta(days=3)] = 900.0  # Tuesday, must be ignored

    def fake_daily(inst_id, start, end):
        rows = [{"date": pd.Timestamp(d), "port_id": 1, "pick_type": "picks",
                 "category": "1", "count": c} for d, c in counts.items()]
        rows.append({"date": pd.Timestamp(target), "port_id": 1,
                     "pick_type": "presentations", "category": "3",
                     "count": 999.0})  # filtered out
        return pd.DataFrame(rows)

    monkeypatch.setattr(pvp, "query_port_wait_time_daily", fake_daily)
    site_map = {"WH": {"Chilled": "inst-91", "Ambient": "inst-92"}}
    # skip the per-day utilisation pulls (network); test the picks/lost logic.
    res = _weekday_kpi_matrix(site_map, "WH", target,
                              months_before=3, months_after=1, with_util=False)
    assert res is not None
    df, best_dates = res
    # only Fridays (no Tuesday), one row per Friday; index carries weekday abbr.
    assert len(df) == len(fridays)
    assert all(i.startswith("Fri ") for i in df.index)
    assert all(date.fromisoformat(i.split()[1]).weekday() == 4 for i in df.index)

    def _lbl(d):
        return f"{d.strftime('%a')} {d.isoformat()}"

    best_lbl = _lbl(target - timedelta(days=7))
    tgt_lbl = _lbl(target)
    assert best_dates[91] == target - timedelta(days=7)
    # best Friday -> 0% lost; today (300/500) -> 40% lost.
    assert round(df.loc[best_lbl, ("AS91", "Lost %")], 1) == 0.0
    assert round(df.loc[tgt_lbl, ("AS91", "Lost %")], 1) == 40.0
    assert df.loc[tgt_lbl, ("AS91", "Picks 1+2")] == 300.0
    # best Friday = 500 picks, today = 300 -> 200 bins lost.
    assert df.loc[tgt_lbl, ("AS91", "Lost bins")] == 200.0
    assert df.loc[best_lbl, ("AS91", "Lost bins")] == 0.0
    # Dig depth column is present (NaN here since util pulls are skipped).
    assert ("AS91", "Dig depth") in df.columns

    # no installation mapping -> None.
    assert _weekday_kpi_matrix({}, "WH", target, with_util=False) is None
    # no data -> None.
    monkeypatch.setattr(pvp, "query_port_wait_time_daily",
                        lambda *a, **k: pd.DataFrame())
    assert _weekday_kpi_matrix(site_map, "WH", target, with_util=False) is None


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
