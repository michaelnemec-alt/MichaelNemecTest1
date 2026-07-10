import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import numbers
import re
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cube_analytics")

from cubeanalytics_utils import (
    is_api_configured, get_installations,
    query_system_health, query_uptime, query_robot_state, query_bin_presentations,
    query_port_wait_time_daily, query_port_uptime, query_incidents, query_robot_errors,
    query_recovery_times, query_installation_data, query_module_versions,
)

SITE_COLORS = [
    "#5B9BD5", "#C5B200", "#8B4C6A", "#1F3864", "#7F7F7F",
    "#6B8E5A", "#2E2E2E", "#A0522D", "#4E8C3F", "#D35400",
]

VIEWS = [
    "Overview & Health",
    "Error & Health Metrics",
    "Performance",
    "Battery & Robots",
    "Health Index *",
]

METRIC_INFO = {
    "System Uptime": "Percentage of time the AutoStore system was operational after accounting for planned recoveries. Formula: recovery_up_ratio x 100. Target: >= 99.7%.",
    "System Availability": "Percentage of time the system was fully available for operations. Formula: up_ratio x 100. Unlike uptime, this does not account for recovery periods.",
    "Robot Availability": "Percentage of total robot time spent in a ready state (available + charging available). A declining trend means more robots are in unavailable, recovery, or service states.",
    "Robot Working %": "Percentage of total robot time spent actively executing tasks. Higher values indicate robots are being utilized more. Complements Robot Availability.",
    "Port Uptime": "Percentage of time the picking/induction ports were operational and available. Calculated per port, then averaged across all ports at each site.",
    "Incident Count": "Number of system incidents (errors, stoppages, or alarms) recorded per day. Lower is better. Spikes may indicate hardware failures or software issues.",
    "Packet Loss": "Percentage of network packets lost in communication between controllers and robots. Target: < 5%. High packet loss causes robot delays and task failures.",
    "MTBF (Mean Time Between Failures)": "Average number of hours the system runs between failures. Higher is better. Measured across all robots and controllers at each site.",
    "MBBD (Mean Bins Between Downtime)": "Average number of bin presentations completed between system downtimes. Higher is better. Measures operational reliability per unit of work.",
    "Wait Time (system-level)": "Average time (seconds) a robot waits at a port before the bin is picked. Target: < 2s. High wait times indicate congestion or slow picking.",
    "Waste Time (system-level)": "Average unproductive time (seconds) per bin presentation. Target: < 0.5s. Includes unnecessary movements, retries, or system overhead.",
    "Bin Wait Time (filtered)": "Average bin wait time filtered by selected pick types and categories. Same as system-level wait time but scoped to specific operations.",
    "User Wait Time (filtered)": "Average time the user (picker) waits for a bin to arrive at the port, filtered by pick type and category.",
    "Waste Time (filtered)": "Average waste time filtered by selected pick types and categories. Target: < 0.5s.",
    "Bin Presentations (filtered)": "Total number of bin presentations (picks) filtered by pick type and category. Shows throughput volume over time.",
    "Average Battery Score": "Average battery health score across all robots at each site. Scale: 1 (poor) to 5 (excellent). Low scores may indicate aging batteries needing replacement.",
    "Health Index": "Overall system health score combining uptime, wait times, waste, battery, and error metrics. Scale: 1 (critical) to 5 (excellent). Target: >= 4.0.",
    "Robot Uptime": "Percentage of total robot time spent in productive or ready states (100% minus recovery, unavailable, service off grid, and parked on grid). Higher is better.",
    "Error Stopped System True": "Count of robot errors per day that caused the AutoStore system to stop. These are critical errors requiring immediate intervention. Lower is better.",
    "Error Stopped System False": "Count of robot errors per day that were resolved without stopping the system. The system continued operating while the error was handled. High counts are normal.",
    "Errors caused by Operations %": "Percentage of total robot errors caused by operations problems (is_bin_quality=True and is_port=False). These are bin handling issues that can be improved through better operational practices.",
    "Errors caused by Facility %": "Percentage of total robot errors caused by facility/technical problems (is_bin_quality=False). These are mechanical or infrastructure issues requiring maintenance or engineering intervention.",
    "Errors caused by Operations": "Count of robot errors caused by operations problems (is_bin_quality=True and is_port=False). Bin handling issues.",
    "Errors caused by Facility": "Count of robot errors caused by facility/technical problems (is_bin_quality=False). Mechanical or infrastructure issues.",
    "Time to Recover - Error Stop": "Median minutes the system was down when force-stopped by a robot error (uptime endpoint, stop code XHANDLER_ROBOT_ERROR_FAILED; down_seconds = STOPPED -> RUNNING). This is a human-response metric: how fast operators recover a hard stop. Lower is better.",
    "Time to Recover - Manual Stop": "Median minutes the system was down when a human stopped it \u2014 an operator console stop (STOPPED_FROM_CONSOLE) or a key switch left disarmed after restart (KEYLOCK_DISARMED). A human-response metric. Lower is better.",
    "Recovery Events - Error Stop": "Number of times the system was force-stopped by a robot error and had to be recovered, per period.",
    "Recovery Events - Manual Stop": "Number of human-initiated (console or key-lock) stop/fix/restart cycles per period.",
    "Bins Outside": "Number of bins located outside the operational grid (e.g. at a port or service position) rather than in a storage cell, from the daily installation-data snapshot. Persistent or rising counts can indicate bins stuck at ports or awaiting rescue.",
}


def _chart_title_with_info(title):
    _title_with_info(title, METRIC_INFO.get(title, "No description available."))


def _title_with_info(title, info):
    info_escaped = info.replace('"', '&quot;').replace("'", "&#39;").replace("\n", "<br>")
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:-8px;">'
        f'<span style="font-size:14px;font-weight:600;color:#1F3864;">{title}</span>'
        f'<span class="info-icon-wrap" style="display:inline-flex;align-items:center;'
        f'justify-content:center;width:18px;height:18px;border-radius:50%;background:#e8edf3;'
        f'color:#5B9BD5;font-size:11px;font-weight:700;cursor:help;position:relative;">'
        f'i<span class="info-tooltip" style="display:none;position:absolute;left:24px;top:-8px;'
        f'background:#1F3864;color:white;padding:10px 14px;border-radius:6px;font-size:12px;'
        f'font-weight:400;width:280px;line-height:1.5;z-index:1000;'
        f'box-shadow:0 4px 12px rgba(0,0,0,0.15);white-space:normal;">'
        f'{info_escaped}</span></span></div>'
        f'<style>'
        f'.info-icon-wrap:hover .info-tooltip {{display:block !important;}}'
        f'</style>',
        unsafe_allow_html=True,
    )


def _hex_to_rgba(hex_color, alpha):
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _make_trend_chart(pivot_df, title, ylabel, threshold=None, threshold_label=None, pct=False):
    fig = go.Figure()
    sites = pivot_df.columns.tolist()
    site_means = {s: pivot_df[s].mean() for s in sites}
    sorted_sites = sorted(sites, key=lambda s: site_means.get(s, 0), reverse=True)
    color_map = {site: SITE_COLORS[i % len(SITE_COLORS)] for i, site in enumerate(sites)}
    highlighted = st.session_state.get("cube_highlight", [])
    highlight_set = {s for s in highlighted if s in sites}
    has_highlight = bool(highlight_set)
    if has_highlight:
        # Draw the highlighted sites last so their solid lines sit on top of the dimmed ones.
        sorted_sites = [s for s in sorted_sites if s not in highlight_set] + \
            [s for s in sorted_sites if s in highlight_set]
    for site in sorted_sites:
        color = color_map[site]
        vals = pivot_df[site]
        short_name = site.split("-", 1)[-1] if "-" in site else site
        hover_fmt = "%{y:.2f}%" if pct else "%{y:.2f}"
        if has_highlight and site not in highlight_set:
            line_color = _hex_to_rgba(color, 0.15)
            line_width = 1.5
            marker = dict(size=3, color=_hex_to_rgba(color, 0.15))
        else:
            line_color = color
            line_width = 3 if has_highlight else 2
            marker = dict(size=5 if has_highlight else 4, color=color)
        fig.add_trace(go.Scatter(
            x=pivot_df.index, y=vals,
            mode="lines+markers", name=short_name,
            line=dict(color=line_color, width=line_width),
            marker=marker,
            hovertemplate=short_name + ": " + hover_fmt + "<extra></extra>",
        ))
    if threshold is not None:
        fig.add_hline(
            y=threshold, line_dash="dash", line_color="#7ab648", line_width=2,
            annotation_text=threshold_label or "Threshold",
            annotation_position="top left",
            annotation_font_color="#7ab648",
        )
    fig.update_layout(
        title=dict(text="", font=dict(size=1)),
        yaxis_title=ylabel,
        xaxis_tickangle=-45,
        xaxis_tickfont=dict(size=9),
        yaxis_tickfont=dict(size=10),
        yaxis_tickformat=".2f" if pct else ".2f",
        legend=dict(font=dict(size=9), orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
        height=380,
        margin=dict(l=60, r=200, t=40, b=60),
        hovermode="x unified",
        hoverlabel=dict(font_size=11),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.add_shape(
        type="rect", xref="paper", yref="paper",
        x0=0, y0=0, x1=1, y1=1,
        line=dict(color="#e0e0e0", width=1),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#eee", gridwidth=1)
    return fig


_WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _current_period_start(agg_mode):
    today = pd.Timestamp.today().normalize()
    if agg_mode == "Week":
        return today - pd.Timedelta(days=today.weekday())
    elif agg_mode == "Month":
        return today.replace(day=1)
    return today


def _aggregate_pivot(df, value_col, agg_mode):
    df = df.copy()
    if agg_mode == "Week":
        df["period"] = df["date"].dt.to_period("W").dt.start_time
    elif agg_mode == "Month":
        df["period"] = df["date"].dt.to_period("M").dt.start_time
    else:
        df["period"] = df["date"]
    if agg_mode in ("Week", "Month"):
        cutoff = _current_period_start(agg_mode)
        df = df[df["period"] < cutoff]
    grouped = df.groupby(["site", "period"])[value_col].mean().reset_index()
    pivot = grouped.pivot(index="period", columns="site", values=value_col).sort_index()
    if agg_mode == "Week":
        pivot.index = pivot.index.strftime("W%V %Y")
    elif agg_mode == "Month":
        pivot.index = pivot.index.strftime("%Y-%m")
    else:
        pivot.index = pivot.index.strftime("%Y-%m-%d")
    return pivot


def _format_pivot_index(pivot, mode):
    if mode in ("Week", "Month"):
        cutoff = _current_period_start(mode)
        pivot = pivot[pivot.index < cutoff]
    if mode == "Week":
        pivot.index = pivot.index.strftime("W%V %Y")
    elif mode == "Month":
        pivot.index = pivot.index.strftime("%Y-%m")
    else:
        pivot.index = pivot.index.strftime("%Y-%m-%d")
    return pivot


def _load_for_sites(query_fn, date_from_str, date_to_str):
    fn_name = getattr(query_fn, "__name__", str(query_fn))
    logger.info("_load_for_sites START: %s (%s to %s)", fn_name, date_from_str, date_to_str)
    t0 = time.time()
    installations = get_installations()
    frames = []

    def _fetch(inst):
        try:
            t1 = time.time()
            df = query_fn(inst["id"], date_from_str, date_to_str)
            logger.info("  %s / %s fetched %d rows in %.1fs", fn_name, inst["name"], len(df), time.time() - t1)
            if not df.empty:
                df["site"] = inst["name"]
                return df
            return None
        except Exception as e:
            logger.error("  %s / %s FAILED: %s", fn_name, inst["name"], e)
            return None

    with ThreadPoolExecutor(max_workers=len(installations)) as pool:
        futures = {pool.submit(_fetch, inst): inst for inst in installations}
        for f in as_completed(futures):
            result = f.result()
            if result is not None:
                frames.append(result)
    result_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    logger.info("_load_for_sites END: %s -> %d rows in %.1fs", fn_name, len(result_df), time.time() - t0)
    return result_df


def _sidebar_controls(heading):
    """Render the shared CUBE sidebar (date range, aggregation, highlight).

    Returns (dt_from, dt_to, aggregation) or (None, None, None) when the
    selection is incomplete/invalid.
    """
    with st.sidebar:
        st.markdown(f"#### {heading}")

        PRESETS = ["Yesterday", "7 days", "14 days", "30 days", "60 days", "90 days", "Custom"]
        if "cube_preset" not in st.session_state:
            st.session_state.cube_preset = "30 days"

        preset = st.segmented_control("Date range", PRESETS,
                                       default=st.session_state.cube_preset,
                                       key="cube_preset_ctrl")
        if preset:
            st.session_state.cube_preset = preset

        active = st.session_state.cube_preset
        if active != "Custom":
            preset_map = {"Yesterday": (1, 1), "7 days": (7, 0), "14 days": (14, 0),
                          "30 days": (30, 0), "60 days": (60, 0), "90 days": (90, 0)}
            days_back, end_off = preset_map.get(active, (30, 0))
            computed_from = date.today() - timedelta(days=days_back)
            computed_to = date.today() - timedelta(days=end_off)
            st.session_state.cube_custom_range = (computed_from, computed_to)

        if "cube_custom_range" not in st.session_state:
            st.session_state.cube_custom_range = (date.today() - timedelta(days=30), date.today())

        date_val = st.date_input("Select dates", value=st.session_state.cube_custom_range,
                                  max_value=date.today(), key="cube_custom_dt")
        if isinstance(date_val, tuple) and len(date_val) == 2:
            dt_from, dt_to = date_val
            if date_val != st.session_state.cube_custom_range:
                st.session_state.cube_custom_range = date_val
                st.session_state.cube_preset = "Custom"
        else:
            dt_from, dt_to = (date_val[0] if isinstance(date_val, tuple) else date_val), None

        st.divider()
        aggregation = st.radio("Aggregation", ["Day", "Week", "Month"], index=1, horizontal=True, key="cube_agg")

        try:
            site_names = sorted(inst["name"] for inst in get_installations())
        except Exception:
            site_names = []
        st.multiselect(
            "Highlight site(s)",
            site_names,
            default=[],
            key="cube_highlight",
            placeholder="All sites",
            format_func=lambda s: s.split("-", 1)[-1] if "-" in s else s,
            help="Keep the selected site(s) solid and fully coloured while the others fade into the background, so you can see how they compare to the rest without hiding them.",
        )

    if not dt_from or not dt_to:
        st.info("Select both start and end dates.")
        return None, None, None
    if dt_from > dt_to:
        st.error("'From' date must be before 'To' date.")
        return None, None, None
    return dt_from, dt_to, aggregation


def render(selected_view="Overview & Health"):
    logger.info("=== render() called with view='%s' ===", selected_view)
    if not is_api_configured():
        st.warning("CubeAnalytics API token not configured. Add `[cubeanalytics] token` to Streamlit secrets.")
        return

    dt_from, dt_to, aggregation = _sidebar_controls("CUBE Analytics")
    if not dt_from:
        return

    date_from_str = str(dt_from)
    date_to_str = str(dt_to)

    try:
        if selected_view == "OEE Overview":
            _view_oee_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to)
        elif selected_view in ("Availability KPI", "Overview & Health"):
            _view_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to)
        elif selected_view in ("Error & Health Metrics", "Uptime metrics"):
            _view_error_health(date_from_str, date_to_str, aggregation)
        elif selected_view == "Performance":
            _view_performance(date_from_str, date_to_str, aggregation)
        elif selected_view == "Battery & Robots":
            _view_battery_robots(date_from_str, date_to_str, aggregation)
        elif selected_view == "Health Index *":
            _view_health_index(date_from_str, date_to_str, aggregation)
        elif selected_view == "Robots":
            _view_module_robots(date_from_str, date_to_str, aggregation)
        elif selected_view == "Ports":
            _view_module_ports(date_from_str, date_to_str, aggregation)
        elif selected_view == "Chargers":
            _view_module_chargers(date_from_str, date_to_str, aggregation)
        elif selected_view == "System":
            _view_module_system(date_from_str, date_to_str, aggregation)
        elif selected_view == "Time to Recover":
            _view_facility_time_to_recover(date_from_str, date_to_str, aggregation)
        elif selected_view == "Reliability":
            _view_facility_reliability(date_from_str, date_to_str, aggregation)
        elif selected_view == "Incidents":
            _view_facility_incidents(date_from_str, date_to_str, aggregation)
        logger.info("=== render() completed successfully for '%s' ===", selected_view)
    except Exception as e:
        logger.error("=== render() CRASHED for '%s': %s ===", selected_view, e)
        logger.error(traceback.format_exc())
        st.error(f"Error loading view: {e}")


def _compute_availability(df_uptime, df_port, df_robot):
    """Per (site, date) System/Port/Robot uptime % and their mean = Availability KPI.

    - System uptime % = uptime recovery_up_ratio × 100
    - Port uptime %   = port-uptime uptime_pct
    - Robot uptime %  = 100 − robot recovery/unavailable/service states
    """
    frames = []
    if not df_uptime.empty and "recovery_up_ratio" in df_uptime.columns:
        u = df_uptime[["site", "date"]].copy()
        u["system_uptime_pct"] = df_uptime["recovery_up_ratio"].values * 100
        frames.append(u)
    if not df_port.empty and "uptime_pct" in df_port.columns:
        frames.append(df_port[["site", "date", "uptime_pct"]].rename(columns={"uptime_pct": "port_uptime_pct"}))
    if not df_robot.empty:
        down = [c for c in ["recovery_pct", "unavailable_pct", "service_off_grid_pct", "service_on_grid_pct"] if c in df_robot.columns]
        if down:
            r = df_robot[["site", "date"]].copy()
            r["robot_uptime_pct"] = 100.0 - df_robot[down].sum(axis=1).values
            frames.append(r)
    if not frames:
        return pd.DataFrame()
    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on=["site", "date"], how="outer")
    comp = [c for c in ["system_uptime_pct", "port_uptime_pct", "robot_uptime_pct"] if c in merged.columns]
    merged["availability_pct"] = merged[comp].mean(axis=1)
    return merged


def _period_window(aggregation):
    today = date.today()
    if aggregation == "Week":
        end = today - timedelta(days=today.isoweekday())
        start = end - timedelta(days=6)
        return start, end, f"W{start.isocalendar()[1]} ({start} — {end})"
    if aggregation == "Month":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1), end, end.strftime("%B %Y")
    start = today - timedelta(days=1)
    return start, start, start.strftime("%Y-%m-%d")


def _view_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to):
    with st.spinner("Loading availability..."):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_uptime = pool.submit(_load_for_sites, query_uptime, date_from_str, date_to_str)
            f_port = pool.submit(_load_for_sites, query_port_uptime, date_from_str, date_to_str)
            f_robot = pool.submit(_load_for_sites, query_robot_state, date_from_str, date_to_str)
        df_uptime = f_uptime.result()
        df_port = f_port.result()
        df_robot = f_robot.result()

    avail = _compute_availability(df_uptime, df_port, df_robot)
    if avail.empty:
        st.warning("No data returned for the selected date range.")
        return

    period_start, period_end, period_label = _period_window(aggregation)
    period_df = avail[(avail["date"].dt.date >= period_start) & (avail["date"].dt.date <= period_end)]
    if period_df.empty:
        period_df = avail[avail["date"] == avail["date"].max()]
        period_label += " (fallback: latest available)"

    label_map = {
        "system_uptime_pct": "System %",
        "port_uptime_pct": "Port %",
        "robot_uptime_pct": "Robot %",
        "availability_pct": "Availability KPI %",
    }
    present = [c for c in label_map if c in period_df.columns]
    latest = period_df.groupby("site")[present].mean().reset_index()
    latest["site"] = latest["site"].apply(_site_code)
    latest = latest.rename(columns={"site": "Site", **label_map})
    latest = latest.sort_values("Availability KPI %", ascending=False).reset_index(drop=True)

    _title_with_info(
        "Availability KPI — All Sites",
        "Availability KPI = mean of System uptime %, Port uptime %, Robot uptime %. "
        "System = running time ÷ total (uptime endpoint, recovery-adjusted). "
        "Port = port operational %. Robot = 100 − robot recovery/unavailable/service time. "
        "This composite feeds the Availability term of OEE.",
    )
    _render_colored_table(
        latest,
        num_cols=list(label_map.values()),
        color_funcs={"Availability KPI %": _color_availability},
    )
    st.caption(f"Period: {period_label}")

    pivot = _aggregate_pivot(avail, "availability_pct", aggregation)
    _chart_title_with_info("Availability KPI")
    st.plotly_chart(_make_trend_chart(pivot, "Availability KPI", "Availability %", pct=True), use_container_width=True)

    for col, title in (
        ("system_uptime_pct", "System Uptime"),
        ("port_uptime_pct", "Port Uptime"),
        ("robot_uptime_pct", "Robot Uptime"),
    ):
        if col in avail.columns:
            pivot = _aggregate_pivot(avail, col, aggregation)
            _chart_title_with_info(title)
            st.plotly_chart(_make_trend_chart(pivot, title, "Uptime %", pct=True), use_container_width=True)

    st.divider()
    csv_bytes = avail.to_csv(index=False).encode("utf-8")
    st.download_button("Download availability data", data=csv_bytes, file_name=f"availability_{dt_from}_{dt_to}.csv", mime="text/csv", key="dl_avail")


def _view_error_health(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading uptime and health data..."):
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_uptime = pool.submit(_load_for_sites, query_uptime, date_from_str, date_to_str)
            f_port = pool.submit(_load_for_sites, query_port_uptime, date_from_str, date_to_str)
            f_robot = pool.submit(_load_for_sites, query_robot_state, date_from_str, date_to_str)
        df_health = f_health.result()
        df_uptime = f_uptime.result()
        df_port_uptime = f_port.result()
        df_robot = f_robot.result()

    if df_health.empty:
        st.warning("No data returned.")
        return

    st.markdown("#### Uptime")

    if not df_uptime.empty:
        df_uptime["system_uptime_pct"] = df_uptime["recovery_up_ratio"] * 100
        pivot = _aggregate_pivot(df_uptime, "system_uptime_pct", aggregation)
        _chart_title_with_info("System Uptime")
        st.plotly_chart(_make_trend_chart(pivot, "System Uptime", "Uptime", threshold=99.7, threshold_label="Target 99.7%", pct=True), use_container_width=True)

    if not df_port_uptime.empty:
        pivot = _aggregate_pivot(df_port_uptime, "uptime_pct", aggregation)
        _chart_title_with_info("Port Uptime")
        st.plotly_chart(_make_trend_chart(pivot, "Port Uptime", "Uptime %", pct=True), use_container_width=True)

    if not df_robot.empty:
        downtime_cols = ["recovery_pct", "unavailable_pct", "service_off_grid_pct", "service_on_grid_pct"]
        available_cols = [c for c in downtime_cols if c in df_robot.columns]
        if available_cols:
            df_robot["robot_uptime_pct"] = 100.0 - df_robot[available_cols].sum(axis=1)
            pivot = _aggregate_pivot(df_robot, "robot_uptime_pct", aggregation)
            _chart_title_with_info("Robot Uptime")
            st.plotly_chart(_make_trend_chart(pivot, "Robot Uptime", "Uptime %", pct=True), use_container_width=True)

    st.divider()
    st.markdown("#### Reliability")

    if "mtbf_h" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "mtbf_h", aggregation)
        _chart_title_with_info("MTBF (Mean Time Between Failures)")
        st.plotly_chart(_make_trend_chart(pivot, "MTBF (Mean Time Between Failures)", "Hours"), use_container_width=True)


def _view_performance(date_from_str, date_to_str, aggregation):
    with st.sidebar:
        selected_weekdays = st.multiselect(
            "Filter to weekday(s)",
            _WEEKDAY_ORDER,
            default=[],
            key="perf_weekday",
            placeholder="All days",
            help="Show only the selected weekday(s), each date side by side "
                 "(e.g. pick Fri to compare Friday-over-Friday). Overrides "
                 "Day/Week/Month for this view.",
        )
    weekday_idx = [_WEEKDAY_ORDER.index(w) for w in selected_weekdays]
    agg_mode = "Day" if weekday_idx else aggregation

    def _filter_weekday(df):
        if weekday_idx and not df.empty and "date" in df.columns:
            return df[df["date"].dt.dayofweek.isin(weekday_idx)]
        return df

    with st.spinner("Loading performance data..."):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_pwt = pool.submit(_load_for_sites, query_port_wait_time_daily, date_from_str, date_to_str)
            f_robot = pool.submit(_load_for_sites, query_robot_state, date_from_str, date_to_str)
        df_health = _filter_weekday(f_health.result())
        df_pwt = _filter_weekday(f_pwt.result())
        df_robot = _filter_weekday(f_robot.result())

    if df_health.empty:
        st.warning("No data returned.")
        return

    st.markdown("#### Performance")

    if "wait_bin" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "wait_bin", agg_mode)
        _chart_title_with_info("Wait Time (system-level)")
        st.plotly_chart(_make_trend_chart(pivot, "Wait Time (system-level)", "Wait Time (s)", threshold=2.0, threshold_label="Target < 2s"), use_container_width=True)

    if "waste_time" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "waste_time", agg_mode)
        _chart_title_with_info("Waste Time (system-level)")
        st.plotly_chart(_make_trend_chart(pivot, "Waste Time (system-level)", "Waste Time (s)", threshold=0.5, threshold_label="Target < 0.5s"), use_container_width=True)

    st.divider()
    st.markdown("**Filtered by Pick Type / Category**")

    if not df_pwt.empty:
        all_pick_types = sorted(df_pwt["pick_type"].dropna().unique().tolist())
        all_categories = sorted(c for c in df_pwt["category"].dropna().unique().tolist() if c != "")

        default_pick_types = [p for p in all_pick_types if p == "picks"]
        default_categories = [c for c in all_categories if c in ("1", "2")]

        with st.sidebar:
            st.divider()
            st.markdown("#### Performance Filters")
            selected_pick_types = st.multiselect(
                "Pick Type", all_pick_types, default=default_pick_types, key="perf_pick_type",
                placeholder="All pick types",
            )
            selected_categories = st.multiselect(
                "Category", all_categories, default=default_categories, key="perf_category",
                placeholder="All categories",
            )

        use_pick = selected_pick_types if selected_pick_types else all_pick_types
        use_cat = selected_categories if selected_categories else all_categories

        df_filtered = df_pwt[
            df_pwt["pick_type"].isin(use_pick) & df_pwt["category"].isin(use_cat)
        ]

        if df_filtered.empty:
            st.info("No data for the selected filters.")
        else:
            wt = df_filtered.copy()
            wt["w_wait_bin"] = wt["average_wait_bin"] * wt["count"]
            wt["w_wait_user"] = wt["average_wait_user"] * wt["count"]
            wt["w_waste"] = wt["average_waste_time"] * wt["count"]

            if agg_mode == "Week":
                wt["period"] = wt["date"].dt.to_period("W").dt.start_time
            elif agg_mode == "Month":
                wt["period"] = wt["date"].dt.to_period("M").dt.start_time
            else:
                wt["period"] = wt["date"]
            if agg_mode in ("Week", "Month"):
                wt = wt[wt["period"] < _current_period_start(agg_mode)]

            agg = wt.groupby(["site", "period"]).agg(
                total_count=("count", "sum"),
                sum_wait_bin=("w_wait_bin", "sum"),
                sum_wait_user=("w_wait_user", "sum"),
                sum_waste=("w_waste", "sum"),
            ).reset_index()
            agg["avg_wait_bin"] = agg["sum_wait_bin"] / agg["total_count"]
            agg["avg_wait_user"] = agg["sum_wait_user"] / agg["total_count"]
            agg["avg_waste"] = agg["sum_waste"] / agg["total_count"]

            for metric, label, ylabel, thresh, thresh_label in [
                ("avg_wait_bin", "Bin Wait Time (filtered)", "Wait Bin (s)", 2.0, "Target < 2s"),
                ("avg_wait_user", "User Wait Time (filtered)", "Wait User (s)", None, None),
                ("avg_waste", "Waste Time (filtered)", "Waste Time (s)", 0.5, "Target < 0.5s"),
            ]:
                piv = agg.pivot(index="period", columns="site", values=metric).sort_index()
                piv = _format_pivot_index(piv, agg_mode)
                _chart_title_with_info(label)
                st.plotly_chart(_make_trend_chart(piv, label, ylabel, threshold=thresh, threshold_label=thresh_label), use_container_width=True)

            piv_count = agg.pivot(index="period", columns="site", values="total_count").sort_index()
            piv_count = _format_pivot_index(piv_count, agg_mode)
            _chart_title_with_info("Bin Presentations (filtered)")
            st.plotly_chart(_make_trend_chart(piv_count, "Bin Presentations (filtered)", "Count"), use_container_width=True)
    else:
        st.info("No port wait time data available.")

    if not df_robot.empty and "working_pct" in df_robot.columns:
        pivot = _aggregate_pivot(df_robot, "working_pct", agg_mode)
        _chart_title_with_info("Robot Working %")
        st.plotly_chart(_make_trend_chart(pivot, "Robot Working %", "% Working", pct=True), use_container_width=True)

    if not df_robot.empty and "robot_availability_pct" in df_robot.columns:
        pivot = _aggregate_pivot(df_robot, "robot_availability_pct", agg_mode)
        _chart_title_with_info("Robot Availability")
        st.plotly_chart(_make_trend_chart(pivot, "Robot Availability", "% Available", pct=True), use_container_width=True)




def _view_battery_robots(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading battery and robot data..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)

    if df_health.empty:
        st.warning("No data returned.")
        return

    st.markdown("#### Battery & Robots")

    if "average_battery_score" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "average_battery_score", aggregation)
        _chart_title_with_info("Average Battery Score")
        st.plotly_chart(_make_trend_chart(pivot, "Average Battery Score", "Score (1-5)"), use_container_width=True)


def _aggregate_pivot_sum(df, value_col, agg_mode):
    df = df.copy()
    if agg_mode == "Week":
        df["period"] = df["date"].dt.to_period("W").dt.start_time
    elif agg_mode == "Month":
        df["period"] = df["date"].dt.to_period("M").dt.start_time
    else:
        df["period"] = df["date"]
    if agg_mode in ("Week", "Month"):
        cutoff = _current_period_start(agg_mode)
        df = df[df["period"] < cutoff]
    grouped = df.groupby(["site", "period"])[value_col].sum().reset_index()
    pivot = grouped.pivot(index="period", columns="site", values=value_col).sort_index()
    if agg_mode == "Week":
        pivot.index = pivot.index.strftime("W%V %Y")
    elif agg_mode == "Month":
        pivot.index = pivot.index.strftime("%Y-%m")
    else:
        pivot.index = pivot.index.strftime("%Y-%m-%d")
    return pivot


def _aggregate_recovery(df, category, agg_mode, how):
    """Aggregate event-level recovery rows into a site x period pivot.

    how='median' -> median recovery time in minutes; how='count' -> number of events.
    """
    df = df[df["category"] == category].copy()
    if df.empty:
        return pd.DataFrame()
    if agg_mode == "Week":
        df["period"] = df["date"].dt.to_period("W").dt.start_time
    elif agg_mode == "Month":
        df["period"] = df["date"].dt.to_period("M").dt.start_time
    else:
        df["period"] = df["date"]
    if agg_mode in ("Week", "Month"):
        df = df[df["period"] < _current_period_start(agg_mode)]
    if df.empty:
        return pd.DataFrame()
    if how == "median":
        grouped = df.groupby(["site", "period"])["recovery_seconds"].median().reset_index()
        grouped["recovery_seconds"] = grouped["recovery_seconds"] / 60.0
    else:
        grouped = df.groupby(["site", "period"])["recovery_seconds"].count().reset_index()
    pivot = grouped.pivot(index="period", columns="site", values="recovery_seconds").sort_index()
    if agg_mode == "Week":
        pivot.index = pivot.index.strftime("W%V %Y")
    elif agg_mode == "Month":
        pivot.index = pivot.index.strftime("%Y-%m")
    else:
        pivot.index = pivot.index.strftime("%Y-%m-%d")
    return pivot


_RECOVERY_META = {
    "recover_error": {
        "category": "error_stop",
        "caption": "System was force-stopped by a robot error (stop code XHANDLER_ROBOT_ERROR_FAILED). "
                   "Recovery = minutes from STOPPED to RUNNING again (median per period).",
        "time_title": "Time to Recover - Error Stop",
        "count_title": "Recovery Events - Error Stop",
    },
    "recover_manual": {
        "category": "manual",
        "caption": "System was stopped by a human \u2014 an operator console stop (STOPPED_FROM_CONSOLE) "
                   "or a key switch left disarmed after restart (KEYLOCK_DISARMED). "
                   "Recovery = minutes from STOPPED to RUNNING (median per period).",
        "time_title": "Time to Recover - Manual Stop",
        "count_title": "Recovery Events - Manual Stop",
    },
}


def _render_recovery_metric(metric_choice, date_from_str, date_to_str, aggregation):
    meta = _RECOVERY_META[metric_choice]
    with st.spinner("Loading recovery data..."):
        df_recovery = _load_for_sites(query_recovery_times, date_from_str, date_to_str)
    st.caption(meta["caption"])
    if df_recovery.empty:
        st.warning("No recovery events found for the selected range.")
        return
    pivot = _aggregate_recovery(df_recovery, meta["category"], aggregation, "median")
    _chart_title_with_info(meta["time_title"])
    if pivot.empty:
        st.info("No matching recovery events in this range.")
        return
    st.plotly_chart(_make_trend_chart(pivot, meta["time_title"], "Minutes"), use_container_width=True)
    pivot_n = _aggregate_recovery(df_recovery, meta["category"], aggregation, "count")
    _chart_title_with_info(meta["count_title"])
    st.plotly_chart(_make_trend_chart(pivot_n, meta["count_title"], "Count"), use_container_width=True)


def _view_health_index(date_from_str, date_to_str, aggregation):
    HEALTH_TABS = ["Health Overview", "Facility vs Ops Overview"]
    health_tab = st.segmented_control(
        "health_nav",
        options=HEALTH_TABS,
        default="Health Overview",
        key="health_tab_selection",
        label_visibility="collapsed",
    )
    if not health_tab:
        health_tab = "Health Overview"

    with st.spinner("Loading health index data..."):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_robot_errors = pool.submit(_load_for_sites, query_robot_errors, date_from_str, date_to_str)
        df_health = f_health.result()
        df_robot_errors = f_robot_errors.result()

    if df_health.empty:
        st.warning("No data returned.")
        return

    if health_tab == "Health Overview":
        st.markdown("#### Health Index")

        pivot = _aggregate_pivot(df_health, "health_index", aggregation)
        _chart_title_with_info("Health Index")
        st.plotly_chart(_make_trend_chart(pivot, "Health Index", "Index (1-5)", threshold=4.0, threshold_label="Target >= 4.0"), use_container_width=True)

        st.divider()
        metric_choice = st.selectbox(
            "Explore other metric",
            ["uptime", "wait_bin", "waste_time", "average_battery_score", "packet_loss", "mtbf_h", "mbbd",
             "recover_error", "recover_manual"],
            format_func=lambda k: {
                "uptime": "Uptime %", "wait_bin": "Wait Bin (s)", "waste_time": "Waste Time (s)",
                "average_battery_score": "Battery Score", "packet_loss": "Packet Loss %",
                "mtbf_h": "MTBF (h)", "mbbd": "MBBD",
                "recover_error": "Time to Recover – Error Stop (min)",
                "recover_manual": "Time to Recover – Manual Stop (min)",
            }.get(k, k),
            key="cube_explore_metric",
        )
        if metric_choice in ("recover_error", "recover_manual"):
            _render_recovery_metric(metric_choice, date_from_str, date_to_str, aggregation)
        elif metric_choice in df_health.columns:
            pct = metric_choice in ("uptime", "packet_loss")
            pivot = _aggregate_pivot(df_health, metric_choice, aggregation)
            st.plotly_chart(_make_trend_chart(pivot, metric_choice.replace("_", " ").title(), metric_choice, pct=pct), use_container_width=True)

    elif health_tab == "Facility vs Ops Overview":
        st.markdown("#### Facility vs Ops Overview")

        if not df_robot_errors.empty:
            st.markdown("#### Operations vs Facility")

            pivot_ops_sum = _aggregate_pivot_sum(df_robot_errors, "ops_errors", aggregation)
            pivot_fac_sum = _aggregate_pivot_sum(df_robot_errors, "facility_errors", aggregation)
            pivot_total = _aggregate_pivot_sum(df_robot_errors, "total_errors", aggregation)

            pivot_ops_pct = (pivot_ops_sum / pivot_total * 100).fillna(0)

            _chart_title_with_info("Errors caused by Operations %")
            st.plotly_chart(_make_trend_chart(pivot_ops_pct, "Errors caused by Operations %", "%", pct=True), use_container_width=True)

            _chart_title_with_info("Errors caused by Operations")
            st.plotly_chart(_make_trend_chart(pivot_ops_sum, "Errors caused by Operations", "Count"), use_container_width=True)

            _chart_title_with_info("Errors caused by Facility")
            st.plotly_chart(_make_trend_chart(pivot_fac_sum, "Errors caused by Facility", "Count"), use_container_width=True)

            st.divider()
            st.markdown("#### Error Stopped System")

            pivot_true = _aggregate_pivot_sum(df_robot_errors, "error_stopped_true", aggregation)
            _chart_title_with_info("Error Stopped System True")
            st.plotly_chart(_make_trend_chart(pivot_true, "Error Stopped System True", "Count"), use_container_width=True)

            pivot_false = _aggregate_pivot_sum(df_robot_errors, "error_stopped_false", aggregation)
            _chart_title_with_info("Error Stopped System False")
            st.plotly_chart(_make_trend_chart(pivot_false, "Error Stopped System False", "Count"), use_container_width=True)


def _view_oee_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to):
    with st.spinner("Loading OEE inputs..."):
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_uptime = pool.submit(_load_for_sites, query_uptime, date_from_str, date_to_str)
            f_port = pool.submit(_load_for_sites, query_port_uptime, date_from_str, date_to_str)
            f_robot = pool.submit(_load_for_sites, query_robot_state, date_from_str, date_to_str)
            f_recovery = pool.submit(_load_for_sites, query_recovery_times, date_from_str, date_to_str)
        df_uptime = f_uptime.result()
        df_port = f_port.result()
        df_robot = f_robot.result()
        df_recovery = f_recovery.result()

    if df_uptime.empty or df_robot.empty:
        st.warning("No data returned for the selected date range.")
        return

    av = _compute_availability(df_uptime, df_port, df_robot)
    if av.empty:
        st.warning("Not enough data to compute Availability.")
        return

    pf = df_robot[["site", "date", "working_pct"]].copy()
    pf = pf.rename(columns={"working_pct": "performance_pct"})

    if not df_recovery.empty:
        rec = df_recovery.copy()
        rec["date"] = pd.to_datetime(rec["date"], errors="coerce").dt.normalize()
        counts = (
            rec.groupby(["site", "date", "category"]).size()
            .unstack(fill_value=0).reset_index()
        )
        for c in ("error_stop", "manual"):
            if c not in counts.columns:
                counts[c] = 0
        counts["total_stops"] = counts["error_stop"] + counts["manual"]
        counts["quality_pct"] = counts.apply(
            lambda r: (1 - r["error_stop"] / r["total_stops"]) * 100 if r["total_stops"] else 100.0,
            axis=1,
        )
        ql = counts[["site", "date", "quality_pct"]]
    else:
        ql = pd.DataFrame(columns=["site", "date", "quality_pct"])

    merged = av[["site", "date", "availability_pct"]].merge(
        pf[["site", "date", "performance_pct"]], on=["site", "date"], how="outer"
    ).merge(ql, on=["site", "date"], how="left")
    merged["quality_pct"] = merged["quality_pct"].fillna(100.0)
    merged = merged.dropna(subset=["availability_pct", "performance_pct"])
    if merged.empty:
        st.warning("Not enough overlapping data to compute OEE.")
        return
    merged["oee_pct"] = (
        merged["availability_pct"] * merged["performance_pct"] * merged["quality_pct"] / 10000.0
    )

    period_start, period_end, period_label = _period_window(aggregation)
    period_df = merged[
        (merged["date"].dt.date >= period_start) & (merged["date"].dt.date <= period_end)
    ]
    if period_df.empty:
        period_df = merged[merged["date"] == merged["date"].max()]
        period_label += " (fallback: latest available)"

    cols = ["availability_pct", "performance_pct", "quality_pct", "oee_pct"]
    latest = period_df.groupby("site")[cols].mean().reset_index()
    latest["site"] = latest["site"].apply(_site_code)
    latest = latest.rename(columns={
        "site": "Site", "availability_pct": "Availability %", "performance_pct": "Performance %",
        "quality_pct": "Quality %", "oee_pct": "OEE %",
    })
    latest = latest.sort_values("OEE %", ascending=False).reset_index(drop=True)

    _title_with_info(
        "OEE — All Sites",
        "OEE = Availability × Performance × Quality, per site. "
        "Availability = composite Availability KPI (mean of System/Port/Robot uptime, "
        "see Availability KPI tab). Performance = share of robot-time spent working "
        "(robot-state). Quality = share of stops that were NOT error-forced (uptime "
        "stop codes); days with no stops count as 100%. Proxies pending official AutoStore targets.",
    )
    _render_colored_table(
        latest,
        num_cols=["Availability %", "Performance %", "Quality %", "OEE %"],
        color_funcs={"OEE %": _color_oee},
    )
    st.caption(f"Period: {period_label}")

    pivot = _aggregate_pivot(merged, "oee_pct", aggregation)
    _chart_title_with_info("OEE")
    st.plotly_chart(_make_trend_chart(pivot, "OEE", "OEE %", pct=True), use_container_width=True)


def _view_module_robots(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading robot metrics..."):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_robot = pool.submit(_load_for_sites, query_robot_state, date_from_str, date_to_str)
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_errors = pool.submit(_load_for_sites, query_robot_errors, date_from_str, date_to_str)
        df_robot = f_robot.result()
        df_health = f_health.result()
        df_robot_errors = f_errors.result()

    st.markdown("#### Robots")
    if df_robot.empty and df_health.empty:
        st.warning("No robot data returned.")
        return

    if not df_robot.empty and "robot_availability_pct" in df_robot.columns:
        pivot = _aggregate_pivot(df_robot, "robot_availability_pct", aggregation)
        _chart_title_with_info("Robot Availability")
        st.plotly_chart(_make_trend_chart(pivot, "Robot Availability", "Availability %", pct=True), use_container_width=True)

    if not df_robot.empty:
        downtime_cols = ["recovery_pct", "unavailable_pct", "service_off_grid_pct", "service_on_grid_pct"]
        available_cols = [c for c in downtime_cols if c in df_robot.columns]
        if available_cols:
            df_robot["robot_uptime_pct"] = 100.0 - df_robot[available_cols].sum(axis=1)
            pivot = _aggregate_pivot(df_robot, "robot_uptime_pct", aggregation)
            _chart_title_with_info("Robot Uptime")
            st.plotly_chart(_make_trend_chart(pivot, "Robot Uptime", "Uptime %", pct=True), use_container_width=True)

    if "average_battery_score" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "average_battery_score", aggregation)
        _chart_title_with_info("Average Battery Score")
        st.plotly_chart(_make_trend_chart(pivot, "Average Battery Score", "Score (1-5)"), use_container_width=True)

    if not df_robot_errors.empty:
        st.divider()
        st.markdown("#### Robot Errors — Operations vs Facility")
        pivot_ops_sum = _aggregate_pivot_sum(df_robot_errors, "ops_errors", aggregation)
        pivot_fac_sum = _aggregate_pivot_sum(df_robot_errors, "facility_errors", aggregation)
        pivot_total = _aggregate_pivot_sum(df_robot_errors, "total_errors", aggregation)
        pivot_ops_pct = (pivot_ops_sum / pivot_total * 100).fillna(0)
        _chart_title_with_info("Errors caused by Operations %")
        st.plotly_chart(_make_trend_chart(pivot_ops_pct, "Errors caused by Operations %", "%", pct=True), use_container_width=True)
        _chart_title_with_info("Errors caused by Operations")
        st.plotly_chart(_make_trend_chart(pivot_ops_sum, "Errors caused by Operations", "Count"), use_container_width=True)
        _chart_title_with_info("Errors caused by Facility")
        st.plotly_chart(_make_trend_chart(pivot_fac_sum, "Errors caused by Facility", "Count"), use_container_width=True)


def _view_module_ports(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading port metrics..."):
        df_port = _load_for_sites(query_port_uptime, date_from_str, date_to_str)

    st.markdown("#### Ports")
    if df_port.empty:
        st.warning("No port data returned.")
        return

    pivot = _aggregate_pivot(df_port, "uptime_pct", aggregation)
    _chart_title_with_info("Port Uptime")
    st.plotly_chart(_make_trend_chart(pivot, "Port Uptime", "Uptime %", pct=True), use_container_width=True)

    if "utilization_pct" in df_port.columns:
        pivot = _aggregate_pivot(df_port, "utilization_pct", aggregation)
        _chart_title_with_info("Port Utilization")
        st.plotly_chart(_make_trend_chart(pivot, "Port Utilization", "Utilization %", pct=True), use_container_width=True)

    with st.spinner("Loading MBBD..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)
    if not df_health.empty and "mbbd" in df_health.columns:
        st.divider()
        pivot = _aggregate_pivot(df_health, "mbbd", aggregation)
        _chart_title_with_info(
            "MBBD (Mean Bins Between Downtime)",
            "Bins processed per port-downtime period (mbbd_bin_count ÷ mbbd_port_downtime_count). "
            "A port-level reliability metric — higher is better.",
        )
        st.plotly_chart(_make_trend_chart(pivot, "MBBD (Mean Bins Between Downtime)", "Bins"), use_container_width=True)


def _view_module_chargers(date_from_str, date_to_str, aggregation):
    st.markdown("#### Chargers")
    st.info(
        "The API exposes no charger availability/uptime figure. `R5-chargers` / `r5-1-charger` "
        "carry per-charger usage, robot interactions and bad-battery/chargehouse flags — wiring "
        "those into first-party charger metrics (charge sessions, charger errors, capacity) is the "
        "next step. For now the closest available signal is robot time spent charging (from robot-state)."
    )
    with st.spinner("Loading charging proxy..."):
        df_robot = _load_for_sites(query_robot_state, date_from_str, date_to_str)
    if not df_robot.empty and "charging_available_pct" in df_robot.columns:
        pivot = _aggregate_pivot(df_robot, "charging_available_pct", aggregation)
        _chart_title_with_info("Robot Charging (available) %")
        st.plotly_chart(_make_trend_chart(pivot, "Robot Charging (available) %", "% of robot-time", pct=True), use_container_width=True)


def _view_module_system(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading system metrics..."):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_uptime = pool.submit(_load_for_sites, query_uptime, date_from_str, date_to_str)
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_incidents = pool.submit(_load_for_sites, query_incidents, date_from_str, date_to_str)
        df_uptime = f_uptime.result()
        df_health = f_health.result()
        df_incidents = f_incidents.result()

    st.markdown("#### System")
    if df_uptime.empty and df_health.empty:
        st.warning("No system data returned.")
        return

    if not df_uptime.empty:
        df_uptime["system_uptime_pct"] = df_uptime["recovery_up_ratio"] * 100
        pivot = _aggregate_pivot(df_uptime, "system_uptime_pct", aggregation)
        _chart_title_with_info("System Uptime")
        st.plotly_chart(_make_trend_chart(pivot, "System Uptime", "Uptime", threshold=99.7, threshold_label="Target 99.7%", pct=True), use_container_width=True)

        df_uptime["system_availability_pct"] = df_uptime["up_ratio"] * 100
        pivot = _aggregate_pivot(df_uptime, "system_availability_pct", aggregation)
        _chart_title_with_info("System Availability")
        st.plotly_chart(_make_trend_chart(pivot, "System Availability", "Availability", pct=True), use_container_width=True)

    if not df_health.empty and "packet_loss" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "packet_loss", aggregation)
        _chart_title_with_info("Packet Loss")
        st.plotly_chart(_make_trend_chart(pivot, "Packet Loss", "Packet Loss %", threshold=5.0, threshold_label="Target < 5%", pct=True), use_container_width=True)

    if not df_incidents.empty:
        pivot = _aggregate_pivot(df_incidents, "incident_count", aggregation)
        _chart_title_with_info("Incident Count")
        st.plotly_chart(_make_trend_chart(pivot, "Incident Count", "Count"), use_container_width=True)


def _view_facility_time_to_recover(date_from_str, date_to_str, aggregation):
    st.markdown("#### Time to Recover")
    _render_recovery_metric("recover_error", date_from_str, date_to_str, aggregation)
    st.divider()
    _render_recovery_metric("recover_manual", date_from_str, date_to_str, aggregation)


def _bins_between_stops_pivot(df_bins, df_recovery, agg_mode, category=None):
    """System-wide bins presented per system stop, per (site, period).

    ratio = sum(bin_presentations) / count(system stops). If `category` is given
    ('error_stop' | 'manual') only those stops count the denominator, else all
    system stops. Periods with zero stops yield NaN (undefined) and drop out.
    """
    def _with_period(df):
        df = df.copy()
        if agg_mode == "Week":
            df["period"] = df["date"].dt.to_period("W").dt.start_time
        elif agg_mode == "Month":
            df["period"] = df["date"].dt.to_period("M").dt.start_time
        else:
            df["period"] = df["date"]
        return df

    b = _with_period(df_bins[["site", "date", "bin_presentations"]])
    r = df_recovery.copy()
    r["date"] = pd.to_datetime(r["date"], errors="coerce")
    if category is not None:
        r = r[r["category"] == category]
    r = _with_period(r[["site", "date"]].assign(stops=1))

    if agg_mode in ("Week", "Month"):
        cutoff = _current_period_start(agg_mode)
        b = b[b["period"] < cutoff]
        r = r[r["period"] < cutoff]

    bins_sum = b.groupby(["site", "period"])["bin_presentations"].sum()
    stops_sum = r.groupby(["site", "period"])["stops"].sum()
    ratio = (bins_sum / stops_sum).replace([float("inf"), -float("inf")], pd.NA).dropna()
    if ratio.empty:
        return pd.DataFrame()
    pivot = ratio.reset_index(name="mbbs").pivot(index="period", columns="site", values="mbbs").sort_index()
    return _format_pivot_index(pivot, agg_mode)


def _view_facility_reliability(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading reliability data..."):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_bins = pool.submit(_load_for_sites, query_bin_presentations, date_from_str, date_to_str)
            f_recovery = pool.submit(_load_for_sites, query_recovery_times, date_from_str, date_to_str)
        df_health = f_health.result()
        df_bins = f_bins.result()
        df_recovery = f_recovery.result()

    st.markdown("#### Reliability")
    if df_health.empty:
        st.warning("No data returned.")
        return

    if "mtbf_h" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "mtbf_h", aggregation)
        _chart_title_with_info("MTBF (Mean Time Between Failures)")
        st.plotly_chart(_make_trend_chart(pivot, "MTBF (Mean Time Between Failures)", "Hours"), use_container_width=True)

    if not df_bins.empty and not df_recovery.empty:
        pivot = _bins_between_stops_pivot(df_bins, df_recovery, aggregation)
        _chart_title_with_info(
            "Bins Between Stops (system)",
            "Whole-system reliability: bins presented ÷ number of system stops "
            "(error-forced + manual). Counts actual system stops from the uptime "
            "endpoint (not the ~200/day port downtimes used by MBBD). Higher is better.",
        )
        if pivot.empty:
            st.info("No system stops in this range — bins-between-stops is undefined.")
        else:
            st.plotly_chart(_make_trend_chart(pivot, "Bins Between Stops (system)", "Bins / stop"), use_container_width=True)

        pivot_err = _bins_between_stops_pivot(df_bins, df_recovery, aggregation, category="error_stop")
        _chart_title_with_info(
            "Bins Between Error Stops (system)",
            "Bins presented ÷ number of error-forced system stops only.",
        )
        if pivot_err.empty:
            st.info("No error stops in this range.")
        else:
            st.plotly_chart(_make_trend_chart(pivot_err, "Bins Between Error Stops (system)", "Bins / stop"), use_container_width=True)


def _view_facility_incidents(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading incidents..."):
        df_incidents = _load_for_sites(query_incidents, date_from_str, date_to_str)

    st.markdown("#### Incidents")
    if df_incidents.empty:
        st.warning("No incident data returned.")
        return

    pivot = _aggregate_pivot(df_incidents, "incident_count", aggregation)
    _chart_title_with_info("Incident Count")
    st.plotly_chart(_make_trend_chart(pivot, "Incident Count", "Count"), use_container_width=True)


AUTOSTORE_VIEWS = ["Versions of Systems", "Bin overview"]

_TABLE_CSS = """
<style>
.as-table-wrap { overflow-x: auto; margin: 4px 0 8px 0; }
.as-table { border-collapse: collapse; font-size: 11px; width: auto; max-width: 100%; }
.as-table th, .as-table td {
    border: 1px solid #eee; padding: 4px 7px; text-align: left; white-space: nowrap;
}
.as-table th { background: #f5f7fa; color: #1F3864; font-weight: 600; }
.as-table th.as-rowhdr, .as-table td.as-rowhdr { background: #fafafa; font-weight: 600; }
.as-table tbody tr:nth-child(even) td { background: #fcfcfd; }
.as-table td.as-outdated { background: #fdecea !important; color: #b02a1a; font-weight: 600; }
</style>
"""


def _render_html_table(df):
    """Render a DataFrame as a static HTML table (bypasses Arrow/st.dataframe)."""
    def _fmt(v):
        if isinstance(v, numbers.Integral) and not isinstance(v, bool):
            return f"{int(v):,}"
        return v

    header = "".join(f"<th>{c}</th>" for c in df.columns)
    rows = []
    for idx, row in df.iterrows():
        cells = "".join(f"<td>{_fmt(v)}</td>" for v in row)
        rows.append(f'<tr><td class="as-rowhdr">{idx}</td>{cells}</tr>')
    index_label = df.index.name or ""
    html = (
        f'{_TABLE_CSS}<div class="as-table-wrap"><table class="as-table">'
        f'<thead><tr><th class="as-rowhdr">{index_label}</th>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _color_oee(val):
    if pd.isna(val):
        return ""
    if val >= 75:
        return "#c6efce"
    if val >= 50:
        return "#ffeb9c"
    return "#ffc7ce"


def _color_availability(val):
    if pd.isna(val):
        return ""
    if val >= 95:
        return "#c6efce"
    if val >= 90:
        return "#ffeb9c"
    return "#ffc7ce"


def _render_colored_table(df, num_cols, color_funcs=None):
    """Render a DataFrame (with a plain 'Site' column) as static HTML, with
    optional per-column background colouring. Bypasses Arrow/st.dataframe."""
    color_funcs = color_funcs or {}
    num_cols = set(num_cols)
    header = "".join(f"<th>{c}</th>" for c in df.columns)
    body = []
    for _, row in df.iterrows():
        cells = []
        for c in df.columns:
            v = row[c]
            style = ""
            if c in color_funcs and pd.notna(v):
                bg = color_funcs[c](v)
                if bg:
                    style = f' style="background-color:{bg}"'
            if c in num_cols:
                txt = "—" if pd.isna(v) else f"{v:.1f}"
            else:
                txt = "—" if pd.isna(v) else str(v)
            cells.append(f"<td{style}>{txt}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    html = (
        f'{_TABLE_CSS}<div class="as-table-wrap"><table class="as-table">'
        f'<thead><tr>{header}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_autostore(selected_view="Versions of Systems"):
    logger.info("=== render_autostore() called with view='%s' ===", selected_view)
    if not is_api_configured():
        st.warning("CubeAnalytics API token not configured. Add `[cubeanalytics] token` to Streamlit secrets.")
        return

    try:
        if selected_view == "Bin overview":
            dt_from, dt_to, aggregation = _sidebar_controls("AutoStore system")
            if not dt_from:
                return
            _view_bin_overview(str(dt_from), str(dt_to), aggregation)
        else:
            with st.sidebar:
                st.markdown("#### AutoStore system")
                st.caption("Showing the latest reported module versions. No date/aggregation filter applies here.")
            dt_to = date.today()
            dt_from = dt_to - timedelta(days=14)
            _view_versions(str(dt_from), str(dt_to))
        logger.info("=== render_autostore() completed for '%s' ===", selected_view)
    except Exception as e:
        logger.error("=== render_autostore() CRASHED for '%s': %s ===", selected_view, e)
        logger.error(traceback.format_exc())
        st.error(f"Error loading view: {e}")


def _short_site(name):
    """Trim the leading numeric code and 'Rohlik-' prefix from a site name."""
    s = re.sub(r"^\d+-", "", str(name))
    s = re.sub(r"^Rohlik-", "", s)
    return s


_SITE_CODES = {
    "Garching Ambient": "MUC_AM",
    "Garching Chilled": "MUC_CH",
    "Praha (Chilled)": "PRG2_CH",
    "Praha (Ambient)": "PRG2_AM",
    "Schönefeld (Chilled)": "BER_CH",
    "Schönefeld (Ambient)": "BER_AM",
    "Vienna (Ambient)": "VIE_AM",
    "Vienna (Chilled)": "VIE_CH",
    "Biatorbágy (Chilled)": "BUD2_CH",
    "Biatorbágy (Ambient)": "BUD2_AM",
}


def _site_code(name):
    """Map a (possibly prefixed) site name to its short code, else the short name."""
    short = _short_site(name)
    return _SITE_CODES.get(short, short)


def _version_key(value):
    """Comparable key for a version string (ignores trailing '*' / non-digits)."""
    parts = re.findall(r"\d+", str(value))
    return tuple(int(p) for p in parts)


def _render_version_table(table):
    """Render the module x site version table, marking outdated cells red.

    A cell is flagged outdated (red) when its version is behind the newest
    version any site in the fleet runs for that module.
    """
    sites = list(table.columns)
    header = "".join(f"<th>{s}</th>" for s in sites)
    rows = []
    for module, row in table.iterrows():
        keys = {s: _version_key(row[s]) for s in sites if str(row[s]) not in ("", "—")}
        max_key = max(keys.values()) if keys else ()
        cells = []
        for s in sites:
            val = row[s]
            cls = ""
            if s in keys and keys[s] and keys[s] < max_key:
                cls = ' class="as-outdated"'
            cells.append(f"<td{cls}>{val}</td>")
        rows.append(f'<tr><td class="as-rowhdr">{module}</td>{"".join(cells)}</tr>')
    html = (
        f'{_TABLE_CSS}<div class="as-table-wrap"><table class="as-table">'
        f'<thead><tr><th class="as-rowhdr">Module</th>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _view_versions(date_from_str, date_to_str):
    with st.spinner("Loading module versions..."):
        df = _load_for_sites(query_module_versions, date_from_str, date_to_str)

    if df.empty:
        st.warning("No module-version data returned for the selected range.")
        return

    latest = df.loc[df.groupby("site")["date"].transform("max") == df["date"]]
    table = latest.pivot_table(
        index="module", columns="site", values="version", aggfunc="first"
    )
    table.columns = [_site_code(s) for s in table.columns]
    table = table.fillna("—")

    _title_with_info(
        "Versions of Systems",
        "Installed module version per site (modules in rows, sites in columns). "
        "A trailing * marks a module reporting more than one distinct version "
        "across its devices. The API exposes only installed versions, so cells "
        "are highlighted red when a site lags behind the newest version any site "
        "in the fleet runs for that module.",
    )
    _render_version_table(table)


def _view_bin_overview(date_from_str, date_to_str, aggregation):
    st.markdown("#### Bin overview")
    with st.spinner("Loading installation data..."):
        df = _load_for_sites(query_installation_data, date_from_str, date_to_str)

    if df.empty:
        st.warning("No installation data returned for the selected range.")
        return

    df_bin = df[df["group"] == "bin"].copy()
    if df_bin.empty:
        st.warning("No bin data returned for the selected range.")
        return

    latest = df_bin.loc[df_bin.groupby("site")["date"].transform("max") == df_bin["date"]]
    type_table = latest[latest["type"] != "outside"].pivot_table(
        index="site", columns="type", values="count", aggfunc="sum"
    )
    outside_now = (
        latest[latest["type"] == "outside"].groupby("site")["count"].sum()
    )
    type_table["Outside"] = outside_now
    type_table.index = [_short_site(s) for s in type_table.index]
    type_table.index.name = "Site"
    type_table = type_table.fillna(0).astype(int)

    df_out = df_bin[df_bin["type"] == "outside"][["date", "site", "count"]].copy()

    col_table, col_chart = st.columns([1, 2.4], gap="small")
    with col_table:
        _title_with_info(
            "Bin inventory",
            "Current bin count per site by bin type, incl. bins outside (latest snapshot in range).",
        )
        _render_html_table(type_table)
    with col_chart:
        _chart_title_with_info("Bins Outside")
        if df_out.empty:
            st.info("No bins-outside data in this range.")
        else:
            pivot = _aggregate_pivot(df_out, "count", aggregation)
            fig = _make_trend_chart(pivot, "Bins Outside", "Bins outside")
            fig.update_layout(
                margin=dict(l=55, r=20, t=30, b=70),
                legend=dict(orientation="h", yanchor="top", y=-0.25, xanchor="left", x=0),
            )
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    with st.spinner("Loading packet loss..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)
    if not df_health.empty and "packet_loss" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "packet_loss", aggregation)
        _chart_title_with_info("Packet Loss")
        st.caption("Parked here temporarily — to be re-homed under an Access Point / network view.")
        st.plotly_chart(_make_trend_chart(pivot, "Packet Loss", "Packet Loss %", threshold=5.0, threshold_label="Target < 5%", pct=True), use_container_width=True)
