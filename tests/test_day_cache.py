"""Tests for the per-day on-disk cache in front of the CubeAnalytics API.

Proves that a repeated / extended range reads immutable past days from disk and
only hits the network for missing or still-recent days. No live API is used —
the network primitive is stubbed and calls are counted.

Run with the app venv: `.venv/bin/python -m pytest tests/` or directly:
`.venv/bin/python tests/test_day_cache.py`.
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cubeanalytics_utils as c

URL = f"{c.BASE_URL}/installations/INST1/robot-state/"


def _iso(d):
    return d.isoformat()


class _Recorder:
    """Stub for _fetch_all_pages: returns one object per day in the requested
    range and records every (after, before) range it was asked to fetch."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((params["after"], params["before"]))
        out = []
        for d in c._iter_days(params["after"], params["before"]):
            out.append({"date": _iso(d), "value": _iso(d)})
        return out

    @property
    def fetched_days(self):
        days = set()
        for a, b in self.calls:
            days.update(c._iter_days(a, b))
        return days


def _with_cache(fn):
    """Run fn with a fresh temp cache dir and a stubbed network primitive."""
    orig_dir = c._DAY_CACHE_DIR
    orig_fetch = c._fetch_all_pages
    rec = _Recorder()
    with tempfile.TemporaryDirectory() as tmp:
        c._DAY_CACHE_DIR = Path(tmp)
        c._fetch_all_pages = rec
        try:
            return fn(rec)
        finally:
            c._DAY_CACHE_DIR = orig_dir
            c._fetch_all_pages = orig_fetch


# All test ranges sit safely in the immutable past so the cache is used.
PAST_TO = date.today() - timedelta(days=10)
PAST_FROM = PAST_TO - timedelta(days=6)  # 7-day window


def test_contiguous_groups():
    d = date(2026, 5, 1)
    days = [d, d + timedelta(days=1), d + timedelta(days=3), d + timedelta(days=4)]
    groups = c._contiguous_groups(days)
    assert [len(g) for g in groups] == [2, 2]
    assert c._contiguous_groups([]) == []


def test_second_identical_range_hits_no_network():
    def run(rec):
        r1 = c._fetch_days(URL, {"after": _iso(PAST_FROM), "before": _iso(PAST_TO)})
        assert len(r1) == 7
        n_after_first = len(rec.calls)
        r2 = c._fetch_days(URL, {"after": _iso(PAST_FROM), "before": _iso(PAST_TO)})
        assert r2 == r1
        assert len(rec.calls) == n_after_first, "second identical range must not re-fetch"
    _with_cache(run)


def test_extended_range_fetches_only_new_days():
    def run(rec):
        c._fetch_days(URL, {"after": _iso(PAST_FROM), "before": _iso(PAST_TO)})
        rec.calls.clear()
        new_to = PAST_TO + timedelta(days=2)
        out = c._fetch_days(URL, {"after": _iso(PAST_FROM), "before": _iso(new_to)})
        assert len(out) == 9
        # Only the 2 genuinely new days should have been requested.
        assert rec.fetched_days == {PAST_TO + timedelta(days=1), new_to}
    _with_cache(run)


def test_results_are_date_ordered():
    def run(rec):
        out = c._fetch_days(URL, {"after": _iso(PAST_FROM), "before": _iso(PAST_TO)})
        dates = [o["date"] for o in out]
        assert dates == sorted(dates)
    _with_cache(run)


def test_recent_tail_reused_while_fresh():
    def run(rec):
        today = date.today()
        frm = today - timedelta(days=3)
        orig_ttl = c._FRESH_TTL_SECONDS
        c._FRESH_TTL_SECONDS = 900  # generous freshness window
        try:
            c._fetch_days(URL, {"after": _iso(frm), "before": _iso(today)})
            rec.calls.clear()
            # Repeat within the freshness window: recent days come from disk.
            c._fetch_days(URL, {"after": _iso(frm), "before": _iso(today)})
            assert not rec.calls, "fresh recent days must be served from disk, not re-fetched"
        finally:
            c._FRESH_TTL_SECONDS = orig_ttl
    _with_cache(run)


def test_recent_tail_refetched_when_stale():
    def run(rec):
        today = date.today()
        frm = today - timedelta(days=3)
        orig_ttl = c._FRESH_TTL_SECONDS
        c._FRESH_TTL_SECONDS = 0  # nothing recent is ever considered fresh
        try:
            c._fetch_days(URL, {"after": _iso(frm), "before": _iso(today)})
            rec.calls.clear()
            c._fetch_days(URL, {"after": _iso(frm), "before": _iso(today)})
            assert rec.calls, "stale recent days must be re-fetched"
            # ...but the immutable past days are still not re-fetched.
            assert today - timedelta(days=3) not in rec.fetched_days
        finally:
            c._FRESH_TTL_SECONDS = orig_ttl
    _with_cache(run)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
