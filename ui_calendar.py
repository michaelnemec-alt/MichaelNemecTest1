"""Shared month-calendar date picker.

A Mon-first month grid where days that have data are shaded and clickable and
days without data are shown plain (non-clickable). Used both by the Prio vs
Picking saved-history calendar and by the Live data day selector so the two
share exactly the same look and "only days with data are selectable" behaviour.
"""
import calendar

import streamlit as st


def date_grid_picker(dates, key_prefix, rerun_scope="app"):
    """Month calendar (Mon-first) where days with stored data are shaded and
    clickable and days without data are plain. Returns the selected date.

    ``rerun_scope`` controls the st.rerun scope on interaction — "fragment"
    when the picker lives inside an st.fragment, "app" (default) otherwise.
    """
    st.html(
        "<style>"
        "div[data-testid='stHorizontalBlock']{gap:0.35rem !important;"
        "margin-bottom:-0.15rem !important}"
        "div[data-testid='stColumn'] button{padding:2px 2px !important;"
        "min-height:0 !important}"
        "div[data-testid='stColumn'] button p{white-space:nowrap !important;"
        "font-size:0.85rem !important;line-height:1.1 !important;margin:0 !important}"
        "</style>")
    available = set(dates)
    newest = dates[-1]
    sel_key = f"{key_prefix}_sel"
    view_key = f"{key_prefix}_view"
    max_key = f"{key_prefix}_max"
    # Land on the newest day with data on first render, and follow it forward
    # whenever a newer day appears (e.g. the collector picks up a new day) — so
    # the picker never stays stuck on an older month. A manual selection of an
    # existing day is still respected until the data set grows past it.
    prev_max = st.session_state.get(max_key)
    if prev_max is None or newest > prev_max:
        st.session_state[sel_key] = newest
        st.session_state[view_key] = (newest.year, newest.month)
    st.session_state[max_key] = newest
    if st.session_state.get(sel_key) not in available:
        st.session_state[sel_key] = newest
    selected = st.session_state[sel_key]
    if view_key not in st.session_state:
        st.session_state[view_key] = (selected.year, selected.month)
    vy, vm = st.session_state[view_key]

    # Keep the grid at ~30% width by parking a wide empty spacer column on the
    # right (nesting real columns inside a layout column isn't allowed).
    spacer = 16
    day_w = [1] * 7 + [spacer]
    c_prev, c_lbl, c_next, _ = st.columns([2, 3, 2, spacer])
    if c_prev.button("◀", key=f"{key_prefix}_prev", use_container_width=True):
        st.session_state[view_key] = (vy - 1, 12) if vm == 1 else (vy, vm - 1)
        st.rerun(scope=rerun_scope)
    c_lbl.markdown(
        f"<div style='text-align:center;font-weight:600;padding-top:6px;"
        f"font-size:0.8rem'>{calendar.month_name[vm]} {vy}</div>",
        unsafe_allow_html=True)
    if c_next.button("▶", key=f"{key_prefix}_next", use_container_width=True):
        st.session_state[view_key] = (vy + 1, 1) if vm == 12 else (vy, vm + 1)
        st.rerun(scope=rerun_scope)

    hdr = st.columns(day_w)
    for i, name in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
        hdr[i].markdown(
            f"<div style='text-align:center;color:#888;font-size:0.7rem'>{name}</div>",
            unsafe_allow_html=True)

    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(vy, vm):
        cols = st.columns(day_w)
        for i, day in enumerate(week):
            if day.month != vm:
                cols[i].markdown("&nbsp;", unsafe_allow_html=True)
            elif day in available:
                if cols[i].button(
                        str(day.day), key=f"{key_prefix}_d_{day.isoformat()}",
                        type="primary" if day == selected else "secondary",
                        use_container_width=True):
                    st.session_state[sel_key] = day
                    st.rerun(scope=rerun_scope)
            else:
                cols[i].markdown(
                    f"<div style='text-align:center;color:#ccc;padding:6px 0'>"
                    f"{day.day}</div>", unsafe_allow_html=True)
    return st.session_state[sel_key]
