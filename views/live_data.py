"""Live data page — explore CubeAnalytics live event stream for one site.

The live-events-stream is a short-retention feed: only the last ~48h are
available (5-min resolution). One site is queried at a time to avoid hammering
the API. A day selector narrows the 48h window to a single calendar day so the
5-min detail stays readable.
"""
import io

import pandas as pd
import streamlit as st

from cubeanalytics_utils import (
    is_api_configured, get_installations, LIVE_EVENT_TYPES,
    query_live_jobs, query_live_robots, query_live_robot_battery,
    query_live_event_table,
)

_EVENT_LABELS = {
    "BIN_AND_TASK": "Jobs (bin & task)",
    "ROBOT_STATE": "Robots (state & battery)",
    "DOOR_STATE": "Doors (grid/robot door state)",
    "DELAYED_SYSTEM_STOP": "Delayed system stops",
    "SYSTEM_MODE": "System mode (running/stopped)",
    "INCIDENT": "Incidents",
    "PORT_STATE": "Ports (state)",
    "PORT_ERROR": "Port errors",
    "ROBOT_ERROR": "Robot errors",
}

_JOB_SERIES = [
    "total", "active", "unique", "total_prepared", "unique_prepared",
    "created", "completed", "updated", "deleted",
]
_ROBOT_SERIES = ["working", "available", "recovery", "unavailable",
                 "charging_available", "charging_unavailable"]


def _days_of(df):
    if df.empty or "ts" not in df.columns:
        return []
    return sorted(df["ts"].dt.date.unique())


def _day_picker(df, key):
    days = _days_of(df)
    if not days:
        return None
    labels = {d.strftime("%a %d %b"): d for d in days}
    choice = st.radio("Day", list(labels.keys()), index=len(labels) - 1,
                      horizontal=True, key=key)
    return labels[choice]


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
    st.line_chart(d.set_index("ts")[["total", "active", "unique",
                                     "total_prepared", "unique_prepared"]])
    st.markdown("**Job flow per 5-min (created / completed / updated / deleted)**")
    st.line_chart(d.set_index("ts")[["created", "completed", "updated", "deleted"]])
    st.markdown("**Data**")
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
    c1, c2, c3 = st.columns(3)
    c1.metric("Robots online", int(d["robots"].max()))
    c2.metric("Avg working", round(float(d["working"].mean()), 1))
    c3.metric("Avg battery %", round(float(d["battery_avg"].mean()), 1)
              if d["battery_avg"].notna().any() else 0)
    st.markdown("**Robots by state — avg concurrent (5-min)**")
    st.line_chart(d.set_index("ts")[[c for c in _ROBOT_SERIES if c in d.columns]])
    if d["battery_avg"].notna().any():
        st.markdown("**Average fleet battery % (5-min)**")
        st.line_chart(d.set_index("ts")[["battery_avg"]])
    if not bat.empty:
        bd = _filter_day(bat, day)
        robot_cols = [c for c in bd.columns if c != "ts"]
        if robot_cols:
            st.markdown(f"**Battery per robot ({len(robot_cols)} robots)**")
            st.line_chart(bd.set_index("ts")[robot_cols])
    st.markdown("**Data**")
    show = ["ts", "robots", "battery_avg", *[c for c in _ROBOT_SERIES if c in d.columns]]
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
    st.bar_chart(counts)
    st.markdown("**Data**")
    st.dataframe(d, use_container_width=True, hide_index=True)
    _download(d, f"{label}_{event_type.lower()}.csv", f"dl_live_{event_type}_page")


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
                                    key="live_site")
    event_type = col_evt.selectbox(
        "Event type", LIVE_EVENT_TYPES,
        format_func=lambda e: _EVENT_LABELS.get(e, e), key="live_event_type")
    inst_id = inst_by_label[site_label]

    st.divider()
    with st.spinner(f"Loading {event_type} (last 48h)..."):
        try:
            if event_type == "BIN_AND_TASK":
                _render_jobs(inst_id, site_label)
            elif event_type == "ROBOT_STATE":
                _render_robots(inst_id, site_label)
            else:
                _render_generic(inst_id, event_type, site_label)
        except Exception as e:
            st.error(f"Live event query failed: {e}")
