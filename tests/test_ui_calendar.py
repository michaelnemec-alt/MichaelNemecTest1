"""Tests for the shared month-calendar picker's selection logic.

Focus: the picker lands on the newest day with data on first render and jumps
forward when a newer day appears (so it never stays stuck on an older month),
while still honouring a manual selection of an existing day. Streamlit is
stubbed with a tiny fake so the pure selection logic can run headless.

Run with the app venv: `.venv/bin/python -m pytest tests/`.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui_calendar


class _Col:
    def button(self, *a, **k):
        return False  # no clicks in these headless tests

    def markdown(self, *a, **k):
        pass


class _FakeSt:
    def __init__(self):
        self.session_state = {}

    def html(self, *a, **k):
        pass

    def markdown(self, *a, **k):
        pass

    def columns(self, spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Col() for _ in range(n)]

    def rerun(self, *a, **k):  # pragma: no cover - buttons never fire here
        raise AssertionError("unexpected rerun")


def _run(fake, dates):
    return ui_calendar.date_grid_picker(dates, key_prefix="k")


def test_defaults_to_newest_day(monkeypatch):
    fake = _FakeSt()
    monkeypatch.setattr(ui_calendar, "st", fake)
    dates = [date(2026, 7, 30), date(2026, 7, 31)]
    assert _run(fake, dates) == date(2026, 7, 31)
    assert fake.session_state["k_view"] == (2026, 7)


def test_jumps_forward_when_newer_day_appears(monkeypatch):
    fake = _FakeSt()
    monkeypatch.setattr(ui_calendar, "st", fake)
    # First render: July only, newest is 31 Jul.
    assert _run(fake, [date(2026, 7, 30), date(2026, 7, 31)]) == date(2026, 7, 31)
    # A newer day (in August) is collected -> picker follows it into August,
    # instead of staying stuck on the July selection.
    dates = [date(2026, 7, 31), date(2026, 8, 1), date(2026, 8, 2)]
    assert _run(fake, dates) == date(2026, 8, 2)
    assert fake.session_state["k_view"] == (2026, 8)


def test_manual_selection_is_respected_until_data_grows(monkeypatch):
    fake = _FakeSt()
    monkeypatch.setattr(ui_calendar, "st", fake)
    dates = [date(2026, 8, 1), date(2026, 8, 2)]
    _run(fake, dates)  # lands on newest (2 Aug)
    # Simulate the user clicking an older existing day.
    fake.session_state["k_sel"] = date(2026, 8, 1)
    # Re-render with the same data set: the manual pick must stick.
    assert _run(fake, dates) == date(2026, 8, 1)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
