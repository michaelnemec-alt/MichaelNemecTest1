"""Live data page — explore CubeAnalytics live event stream for one site.

The live-events-stream is a short-retention feed: only the last ~48h are
available (5-min resolution). One site is queried at a time to avoid hammering
the API. A day selector narrows the 48h window to a single calendar day so the
5-min detail stays readable.
"""
import io
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from cubeanalytics_utils import (
    is_api_configured, get_installations, site_display_label, LIVE_EVENT_TYPES,
    query_live_jobs, query_live_robots, query_live_robot_battery,
    query_live_event_table, query_access_point_load,
)
import raw_events
from ui_calendar import date_grid_picker

_ACCESS_POINT_LOAD = "ACCESS_POINT_LOAD"
_CHARGER_STATE = "CHARGER_STATE"

_EVENT_LABELS = {
    "BIN_AND_TASK": "Jobs (bin & task)",
    "ROBOT_STATE": "Robots (state & battery)",
    _CHARGER_STATE: "Chargers (state, collected)",
    "DOOR_STATE": "Doors (grid/robot door state)",
    "DELAYED_SYSTEM_STOP": "Delayed system stops",
    "SYSTEM_MODE": "System mode (running/stopped)",
    "INCIDENT": "Incidents",
    "PORT_STATE": "Ports (state)",
    "PORT_ERROR": "Port errors",
    "ROBOT_ERROR": "Robot errors",
    _ACCESS_POINT_LOAD: "Access point load (radio, hourly)",
}

_EVENT_TYPES = list(LIVE_EVENT_TYPES) + [_CHARGER_STATE, _ACCESS_POINT_LOAD]
_CHARGER_STATES = ["charging", "on", "off", "error"]

_JOB_SERIES = [
    "total", "active", "unique", "total_prepared", "unique_prepared",
    "created", "completed", "updated", "deleted",
]
_ROBOT_SERIES = ["working", "available", "recovery", "unavailable",
                 "charging_available", "charging_unavailable"]

# Robots-by-state stacked column: order is bottom->top and colours mirror the
# CubeAnalytics portal "state development of robots" view.
_ROBOT_STACK = [
    ("available", "Available for work", "#9ecae1"),
    ("charging_available", "Charging, available for work", "#08519c"),
    ("working", "Working", "#74c476"),
    ("charging_unavailable", "Charging, not available for work", "#fd8d3c"),
    ("recovery", "Recovery", "#969696"),
    ("unavailable", "Unavailable", "#d62728"),
]


def _line_chart(df, cols, decimals=1):
    """Native Streamlit line chart (keeps fullscreen/hover). Values are rounded
    to `decimals` so the hover tooltip never shows long floats."""
    data = df.set_index("ts")[[c for c in cols if c in df.columns]].round(decimals)
    st.line_chart(data)


def _bar_chart(series):
    st.bar_chart(series.round(0))


def _robot_state_bar(df, stack):
    """Stacked column of robots-by-state. `stack` is a list of
    (column, label, colour) bottom->top. Uses Altair so both the colours and
    the stack order are fixed (native st.bar_chart sorts them alphabetically).
    tz-naive timestamps render literally on a 24h axis."""
    cols = [col for col, _, _ in stack]
    labels = [label for _, label, _ in stack]
    colours = [colour for _, _, colour in stack]
    # Fleet total per timestamp = sum across every state present (not just the
    # picked ones), so the share is always relative to the whole fleet.
    all_cols = [c for c, _, _ in _ROBOT_STACK if c in df.columns]
    totals = df.set_index("ts")[all_cols].sum(axis=1)
    data = df[["ts"] + cols].copy().round(1)
    data.columns = ["ts"] + labels
    long = data.melt("ts", var_name="state", value_name="robots")
    order = {label: i for i, label in enumerate(labels)}
    long["order"] = long["state"].map(order)
    total_by_ts = long["ts"].map(totals)
    share = (long["robots"] / total_by_ts.replace(0, pd.NA) * 100)
    long["value"] = [
        f"{r:.1f} ({s:.0f}%)" if pd.notna(s) else f"{r:.1f}"
        for r, s in zip(long["robots"], share)
    ]
    chart = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("ts:T", title=None,
                    axis=alt.Axis(format="%H:%M", labelOverlap=True)),
            y=alt.Y("robots:Q", title=None, stack=True),
            color=alt.Color("state:N", title=None, sort=labels,
                            scale=alt.Scale(domain=labels, range=colours)),
            order=alt.Order("order:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("ts:T", title="time", format="%H:%M"),
                alt.Tooltip("state:N", title="state"),
                alt.Tooltip("value:N", title="robots (%)"),
            ],
        )
        .properties(height=320)
        .configure_axisX(labelAngle=0)
    )
    # Tighten the Vega tooltip so its rows sit close together and it stays small.
    st.markdown(
        "<style>#vg-tooltip-element{font-size:12px!important;padding:4px 6px"
        "!important}#vg-tooltip-element table{border-spacing:0!important}"
        "#vg-tooltip-element td{padding:0 4px 0 0!important;line-height:1.15"
        "!important}#vg-tooltip-element tr>td.key{padding-right:6px!important}"
        "</style>",
        unsafe_allow_html=True)
    st.altair_chart(chart, use_container_width=True)


def _days_of(df):
    if df.empty or "ts" not in df.columns:
        return []
    return sorted(df["ts"].dt.date.unique())


def _day_picker(df, key):
    days = _days_of(df)
    if not days:
        return None
    st.markdown("**Day** — shaded days have data")
    return date_grid_picker(days, key_prefix=key)


def _filter_day(df, day):
    if df.empty or day is None:
        return df
    return df[df["ts"].dt.date == day].reset_index(drop=True)


def _csv_bytes(df):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def _download(df, name, key):
    st.download_button(f"Download CSV — {name}", data=_csv_bytes(df),
                       file_name=name, mime="text/csv", key=key)


def _render_jobs(inst_id, label):
    df = query_live_jobs(inst_id, 48)
    if df.empty:
        st.warning("No live BIN_AND_TASK events for this site.")
        return
    day = _day_picker(df, "live_jobs_day")
    d = _filter_day(df, day)
    if d.empty:
        st.info("No events for the selected day.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Created (day)", int(d["created"].sum()))
    c2.metric("Completed (day)", int(d["completed"].sum()))
    c3.metric("Peak active", int(d["active"].max()))
    c4.metric("5-min events", len(d))
    st.markdown("**Jobs in the system (5-min)**")
    _line_chart(d, ["total", "active", "unique", "total_prepared", "unique_prepared"])
    st.markdown("**Job flow per 5-min (created / completed / updated / deleted)**")
    _line_chart(d, ["created", "completed", "updated", "deleted"])
    with st.expander("Data table + CSV"):
        st.dataframe(d[["ts", *_JOB_SERIES]], use_container_width=True, hide_index=True)
        _download(d[["ts", *_JOB_SERIES]], f"{label}_jobs.csv", "dl_live_jobs_page")


def _render_robots(inst_id, label):
    df = query_live_robots(inst_id, 48)
    bat = query_live_robot_battery(inst_id, 48)
    if df.empty:
        st.warning("No live ROBOT_STATE events for this site.")
        return
    day = _day_picker(df, "live_robots_day")
    d = _filter_day(df, day)
    if d.empty:
        st.info("No events for the selected day.")
        return
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("Robots online", int(d["robots"].max()))
        c2.metric("Avg working", round(float(d["working"].mean()), 1))
        c3.metric("Avg battery %", round(float(d["battery_avg"].mean()), 1)
                  if d["battery_avg"].notna().any() else 0)
    with st.container(border=True):
        st.markdown("**Robots by state — avg concurrent (5-min)**")
        present = [(col, label, color) for col, label, color in _ROBOT_STACK
                   if col in d.columns]
        labels = [label for _, label, _ in present]
        picked = st.multiselect(
            "States to show", labels, default=labels, key="live_robot_states")
        stack = [t for t in present if t[1] in picked]
        if stack:
            _robot_state_bar(d, stack)
        else:
            st.info("Select at least one state to show.")
    if d["battery_avg"].notna().any():
        with st.container(border=True):
            st.markdown("**Average fleet battery % (5-min)**")
            _line_chart(d, ["battery_avg"])
    if not bat.empty:
        bd = _filter_day(bat, day)
        robot_cols = [c for c in bd.columns if c != "ts"]
        if robot_cols:
            with st.container(border=True):
                st.markdown(f"**Battery per robot ({len(robot_cols)} robots)**")
                _line_chart(bd, robot_cols)
    show = ["ts", "robots", "battery_avg", *[c for c in _ROBOT_SERIES if c in d.columns]]
    with st.expander("Data table + CSV"):
        st.dataframe(d[show], use_container_width=True, hide_index=True)
        _download(d[show], f"{label}_robots.csv", "dl_live_robots_page")


def _render_generic(inst_id, event_type, label):
    df = query_live_event_table(inst_id, event_type, 48)
    if df.empty:
        st.warning(f"No live {event_type} events for this site in the last 48h.")
        return
    day = _day_picker(df, f"live_{event_type}_day")
    d = _filter_day(df, day)
    if d.empty:
        st.info("No events for the selected day.")
        return
    counts = (d.set_index("ts").assign(records=1)
              .resample("5min")["records"].sum())
    c1, c2 = st.columns(2)
    c1.metric("Records (day)", len(d))
    c2.metric("Distinct 5-min buckets", int((counts > 0).sum()))
    st.markdown("**Records per 5-min**")
    _bar_chart(counts)
    with st.expander("Data table + CSV"):
        st.dataframe(d, use_container_width=True, hide_index=True)
        _download(d, f"{label}_{event_type.lower()}.csv", f"dl_live_{event_type}_page")


def _render_chargers(inst_id, label):
    if not raw_events.is_available():
        st.warning(
            "Charger data comes from the night collector (CHARGER_STATE is "
            "WebSocket-only and not served by the REST live stream). The "
            "collector store is not mounted here yet, so no charger data is "
            "available.")
        return
    df = raw_events.read_charger_state(inst_id, 48)
    if df.empty:
        st.info("No collected CHARGER_STATE for this site yet — the collector "
                "starts filling once it has been running.")
        return
    day = _day_picker(df, "live_charger_day")
    d = _filter_day(df, day)
    if d.empty:
        st.info("No events for the selected day.")
        return
    conc = raw_events.charger_state_concurrent(d)
    latest_ts = d["ts"].max()
    snap = d[d["ts"] == latest_ts]
    snap_counts = snap["state"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Chargers", int(d["charger_id"].nunique()))
    c2.metric("Charging now", int(snap_counts.get("charging", 0)))
    c3.metric("On now", int(snap_counts.get("on", 0)))
    c4.metric("Off / error now",
              int(snap_counts.get("off", 0) + snap_counts.get("error", 0)))
    by_type = (snap.groupby("charger_type")["charger_id"].nunique()
               .sort_values(ascending=False))
    type_str = ", ".join(f"{n}× {t}" for t, n in by_type.items())
    st.caption(f"'Now' = latest snapshot {latest_ts:%H:%M} (site local time). "
               f"Charger types: {type_str}.")
    lithium = [t for t in by_type.index if "QUICK" in t.upper()]
    non_lithium = [t for t in by_type.index if "QUICK" not in t.upper()]
    if non_lithium:
        st.info(
            "Note: only lithium chargers (e.g. **QUICK_V1**) report `charging` "
            "seconds and temperatures. **R5/R5+ charge points** report only "
            "on/off, so their `charging` shows 0 and temperature is empty even "
            "while robots are physically charging. For fleet charging use the "
            "**Robots (state & battery)** event (`charging_available` / "
            "`charging_unavailable`), which is reported for every site."
            + ("" if lithium else
               " This site has no lithium chargers, so `charging` here is "
               "always 0 by design."))
    st.markdown("**Chargers by state — avg concurrent (5-min)**")
    _line_chart(conc, [s for s in _CHARGER_STATES if s in conc.columns])
    if conc["temp_max"].notna().any():
        st.markdown("**Max charger/battery temperature (°C, 5-min)**")
        _line_chart(conc, ["temp_max"])
    else:
        st.caption("No temperature readings reported by these chargers "
                   "(R5 charge points do not send temperatures).")
    st.markdown("**State per charger (latest snapshot)**")
    st.dataframe(
        snap[["charger_id", "charger_type", "state", "temp_max", *_CHARGER_STATES]]
        .sort_values("charger_id"),
        use_container_width=True, hide_index=True)
    with st.expander("Data (5-min, per charger) + CSV"):
        st.dataframe(d, use_container_width=True, hide_index=True)
        _download(d, f"{label}_charger_state.csv", "dl_live_charger_page")


def _render_access_point_load(inst_id, label):
    today = date.today()
    df = query_access_point_load(inst_id, str(today - timedelta(days=3)),
                                 str(today + timedelta(days=1)))
    if df.empty:
        st.warning("No access-point-load data for this site.")
        return
    day = _day_picker(df, "live_apl_day")
    d = _filter_day(df, day)
    if d.empty:
        st.info("No data for the selected day.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Access points", d["ap_id"].nunique())
    c2.metric("Avg load", round(float(d["load"].mean()), 1))
    c3.metric("Peak load", int(d["peak_ap_load"].max()))
    st.markdown("**Average load per access point (hourly)**")
    avg = d.pivot_table(index="ts", columns="ap_id", values="load", aggfunc="mean")
    _line_chart(avg.reset_index(), list(avg.columns))
    st.markdown("**Peak load per access point (hourly)**")
    peak = d.pivot_table(index="ts", columns="ap_id", values="peak_ap_load", aggfunc="max")
    _line_chart(peak.reset_index(), list(peak.columns))
    with st.expander("Data table + CSV"):
        st.dataframe(d, use_container_width=True, hide_index=True)
        _download(d, f"{label}_access_point_load.csv", "dl_live_apl_page")


def render():
    st.markdown("### Live data")
    st.caption("CubeAnalytics live event stream for one site — last ~48h, "
               "5-min resolution. Pick a site, an event type, and a day. "
               "Live/rolling data with short retention (not full history).")

    if not is_api_configured():
        st.info("CubeAnalytics API not configured — live data unavailable.")
        return

    try:
        installations = get_installations()
    except Exception as e:
        st.error(f"Could not load installations: {e}")
        return
    if not installations:
        st.warning("No installations available for this token.")
        return

    inst_by_label = {i["name"]: i["id"]
                     for i in sorted(installations, key=lambda x: x["name"])}
    col_site, col_evt = st.columns([2, 1])
    site_label = col_site.selectbox("Site", list(inst_by_label.keys()),
                                    format_func=site_display_label,
                                    key="live_site")
    event_type = col_evt.selectbox(
        "Event type", _EVENT_TYPES,
        format_func=lambda e: _EVENT_LABELS.get(e, e), key="live_event_type")
    inst_id = inst_by_label[site_label]

    st.divider()
    with st.spinner(f"Loading {event_type}..."):
        try:
            if event_type == "BIN_AND_TASK":
                _render_jobs(inst_id, site_label)
            elif event_type == "ROBOT_STATE":
                _render_robots(inst_id, site_label)
            elif event_type == _CHARGER_STATE:
                _render_chargers(inst_id, site_label)
            elif event_type == _ACCESS_POINT_LOAD:
                _render_access_point_load(inst_id, site_label)
            else:
                _render_generic(inst_id, event_type, site_label)
        except Exception as e:
            st.error(f"Live event query failed: {e}")
