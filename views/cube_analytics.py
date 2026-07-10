import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import time
import traceback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cube_analytics")

from cubeanalytics_utils import (
    is_api_configured, get_installations,
    query_system_health, query_uptime, query_robot_state, query_bin_presentations,
    query_port_wait_time_daily, query_port_uptime, query_incidents, query_robot_errors,
    query_recovery_times,
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
    "Time to Recover - Error Stop": "Median minutes to get the system RUNNING again after it was force-stopped by a robot error (event-log stop code XHANDLER_ROBOT_ERROR_FAILED -> next RUNNING). This is a human-response metric: how fast operators recover a hard stop. Lower is better.",
    "Time to Recover - Manual/Delayed Stop": "Median minutes for the stop-fix-restart cycle when the system tolerated an error (delayed stop) but kept running, then an operator manually stopped it (STOPPED_FROM_CONSOLE within 35 min of the delayed-stop error) to fix it and restarted. A human-response metric. Lower is better.",
    "Recovery Events - Error Stop": "Number of times the system was force-stopped by a robot error and had to be recovered, per period.",
    "Recovery Events - Manual/Delayed Stop": "Number of delayed-stop-linked manual stop/fix/restart cycles per period.",
}


def _chart_title_with_info(title):
    info = METRIC_INFO.get(title, "No description available.")
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
    highlight = st.session_state.get("cube_highlight", "All sites")
    has_highlight = highlight and highlight != "All sites" and highlight in sites
    if has_highlight:
        # Draw the highlighted site last so its solid line sits on top of the dimmed ones.
        sorted_sites = [s for s in sorted_sites if s != highlight] + [highlight]
    for site in sorted_sites:
        color = color_map[site]
        vals = pivot_df[site]
        short_name = site.split("-", 1)[-1] if "-" in site else site
        hover_fmt = "%{y:.2f}%" if pct else "%{y:.2f}"
        if has_highlight and site != highlight:
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


_WEEKDAY_LABELS = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}


def _current_period_start(agg_mode):
    today = pd.Timestamp.today().normalize()
    if agg_mode == "Week":
        return today - pd.Timedelta(days=today.weekday())
    elif agg_mode == "Month":
        return today.replace(day=1)
    return today


def _aggregate_pivot(df, value_col, agg_mode):
    df = df.copy()
    if agg_mode == "DayOfWeek":
        df["period"] = df["date"].dt.dayofweek
        grouped = df.groupby(["site", "period"])[value_col].mean().reset_index()
        pivot = grouped.pivot(index="period", columns="site", values=value_col).sort_index()
        pivot.index = pivot.index.map(_WEEKDAY_LABELS)
        return pivot
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
    if mode == "DayOfWeek":
        pivot = pivot.sort_index()
        pivot.index = pivot.index.map(_WEEKDAY_LABELS)
        return pivot
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


def render(selected_view="Overview & Health"):
    logger.info("=== render() called with view='%s' ===", selected_view)
    if not is_api_configured():
        st.warning("CubeAnalytics API token not configured. Add `[cubeanalytics] token` to Streamlit secrets.")
        return

    with st.sidebar:
        st.markdown("#### CUBE Analytics")

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
        highlight_options = ["All sites"] + site_names
        st.selectbox(
            "Highlight site",
            highlight_options,
            index=0,
            key="cube_highlight",
            format_func=lambda s: s if s == "All sites" else (s.split("-", 1)[-1] if "-" in s else s),
            help="Keep one site solid and fully coloured while the others fade into the background, so you can see how it compares to the rest without hiding them.",
        )

    if not dt_from or not dt_to:
        st.info("Select both start and end dates.")
        return
    if dt_from > dt_to:
        st.error("'From' date must be before 'To' date.")
        return

    date_from_str = str(dt_from)
    date_to_str = str(dt_to)

    try:
        if selected_view == "Overview & Health":
            _view_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to)
        elif selected_view == "Error & Health Metrics":
            _view_error_health(date_from_str, date_to_str, aggregation)
        elif selected_view == "Performance":
            _view_performance(date_from_str, date_to_str, aggregation)
        elif selected_view == "Battery & Robots":
            _view_battery_robots(date_from_str, date_to_str, aggregation)
        elif selected_view == "Health Index *":
            _view_health_index(date_from_str, date_to_str, aggregation)
        logger.info("=== render() completed successfully for '%s' ===", selected_view)
    except Exception as e:
        logger.error("=== render() CRASHED for '%s': %s ===", selected_view, e)
        logger.error(traceback.format_exc())
        st.error(f"Error loading view: {e}")


def _view_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to):
    with st.spinner("Loading system health..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)

    if df_health.empty:
        st.warning("No data returned for the selected date range.")
        return

    today = date.today()
    if aggregation == "Week":
        period_end = today - timedelta(days=today.isoweekday())
        period_start = period_end - timedelta(days=6)
        period_label = f"W{period_start.isocalendar()[1]} ({period_start} — {period_end})"
    elif aggregation == "Month":
        first_of_month = today.replace(day=1)
        period_end = first_of_month - timedelta(days=1)
        period_start = period_end.replace(day=1)
        period_label = period_start.strftime("%B %Y")
    else:
        period_start = today - timedelta(days=1)
        period_end = period_start
        period_label = period_start.strftime("%Y-%m-%d")

    period_df = df_health[
        (df_health["date"].dt.date >= period_start) & (df_health["date"].dt.date <= period_end)
    ]
    if period_df.empty:
        period_df = df_health[df_health["date"] == df_health["date"].max()]
        period_label += " (fallback: latest available)"

    metrics = ["health_index", "uptime", "wait_bin", "waste_time",
               "average_battery_score", "mtbf_h", "packet_loss", "mbbd"]
    latest = period_df.groupby("site")[metrics].mean().reset_index()
    latest.columns = ["Site", "Health", "Uptime %", "Wait (s)", "Waste (s)", "Battery", "MTBF (h)", "Pkt Loss %", "MBBD"]
    latest = latest.sort_values("Health", ascending=False).reset_index(drop=True)

    st.markdown(f"#### Health — All Sites")

    latest["Health"] = latest["Health"].round(2)
    latest["Uptime %"] = latest["Uptime %"].round(2)
    latest["Wait (s)"] = latest["Wait (s)"].round(1)
    latest["Waste (s)"] = latest["Waste (s)"].round(2)
    latest["Battery"] = latest["Battery"].round(2)
    latest["MTBF (h)"] = latest["MTBF (h)"].apply(lambda x: int(x) if pd.notna(x) else None)
    latest["Pkt Loss %"] = latest["Pkt Loss %"].round(2)
    latest["MBBD"] = latest["MBBD"].apply(lambda x: int(x) if pd.notna(x) else None)

    def _color_health(val):
        if pd.isna(val):
            return ""
        if val >= 4.5:
            return "background-color: #c6efce"
        if val >= 3.5:
            return "background-color: #ffeb9c"
        return "background-color: #ffc7ce"

    def _color_uptime(val):
        if pd.isna(val):
            return ""
        if val >= 99.9:
            return "background-color: #c6efce"
        if val >= 99.0:
            return "background-color: #ffeb9c"
        return "background-color: #ffc7ce"

    styled = (latest.style
        .map(_color_health, subset=["Health"])
        .map(_color_uptime, subset=["Uptime %"])
        .format({
            "Health": "{:.2f}",
            "Uptime %": "{:.2f}",
            "Wait (s)": "{:.1f}",
            "Waste (s)": "{:.2f}",
            "Battery": "{:.2f}",
            "MTBF (h)": lambda x: str(int(x)) if pd.notna(x) else "None",
            "Pkt Loss %": "{:.2f}",
            "MBBD": lambda x: str(int(x)) if pd.notna(x) else "None",
        })
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption(f"Period: {period_label}")

    pivot = _aggregate_pivot(df_health, "health_index", aggregation)
    _chart_title_with_info("Health Index")
    st.plotly_chart(_make_trend_chart(pivot, "Health Index", "Index (1-5)", threshold=4.0, threshold_label="Target >= 4.0"), use_container_width=True)

    st.divider()
    csv_bytes = df_health.to_csv(index=False).encode("utf-8")
    st.download_button("Download health data", data=csv_bytes, file_name=f"health_{dt_from}_{dt_to}.csv", mime="text/csv", key="dl_health")


def _view_error_health(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading uptime and health data..."):
        with ThreadPoolExecutor(max_workers=5) as pool:
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_uptime = pool.submit(_load_for_sites, query_uptime, date_from_str, date_to_str)
            f_port = pool.submit(_load_for_sites, query_port_uptime, date_from_str, date_to_str)
            f_incidents = pool.submit(_load_for_sites, query_incidents, date_from_str, date_to_str)
            f_robot = pool.submit(_load_for_sites, query_robot_state, date_from_str, date_to_str)
        df_health = f_health.result()
        df_uptime = f_uptime.result()
        df_port_uptime = f_port.result()
        df_incidents = f_incidents.result()
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
    st.markdown("#### Availability")

    if not df_uptime.empty:
        df_uptime["system_availability_pct"] = df_uptime["up_ratio"] * 100
        pivot = _aggregate_pivot(df_uptime, "system_availability_pct", aggregation)
        _chart_title_with_info("System Availability")
        st.plotly_chart(_make_trend_chart(pivot, "System Availability", "Availability", pct=True), use_container_width=True)

    st.divider()
    st.markdown("#### Errors & Reliability")

    if not df_incidents.empty:
        pivot = _aggregate_pivot(df_incidents, "incident_count", aggregation)
        _chart_title_with_info("Incident Count")
        st.plotly_chart(_make_trend_chart(pivot, "Incident Count", "Count"), use_container_width=True)

    if "packet_loss" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "packet_loss", aggregation)
        _chart_title_with_info("Packet Loss")
        st.plotly_chart(_make_trend_chart(pivot, "Packet Loss", "Packet Loss %", threshold=5.0, threshold_label="Target < 5%", pct=True), use_container_width=True)

    if "mtbf_h" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "mtbf_h", aggregation)
        _chart_title_with_info("MTBF (Mean Time Between Failures)")
        st.plotly_chart(_make_trend_chart(pivot, "MTBF (Mean Time Between Failures)", "Hours"), use_container_width=True)

    if "mbbd" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "mbbd", aggregation)
        _chart_title_with_info("MBBD (Mean Bins Between Downtime)")
        st.plotly_chart(_make_trend_chart(pivot, "MBBD (Mean Bins Between Downtime)", "Bins"), use_container_width=True)


def _view_performance(date_from_str, date_to_str, aggregation):
    with st.sidebar:
        dow_mode = st.checkbox(
            "Aggregate by day of week",
            key="perf_dow",
            help="Group the selected period by weekday (Mon–Sun) to focus on peak days. Overrides Day/Week/Month for this view.",
        )
    agg_mode = "DayOfWeek" if dow_mode else aggregation

    with st.spinner("Loading performance data..."):
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_health = pool.submit(_load_for_sites, query_system_health, date_from_str, date_to_str)
            f_pwt = pool.submit(_load_for_sites, query_port_wait_time_daily, date_from_str, date_to_str)
            f_robot = pool.submit(_load_for_sites, query_robot_state, date_from_str, date_to_str)
        df_health = f_health.result()
        df_pwt = f_pwt.result()
        df_robot = f_robot.result()

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
            elif agg_mode == "DayOfWeek":
                wt["period"] = wt["date"].dt.dayofweek
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
        "category": "manual_delayed",
        "caption": "System tolerated an error and kept running, then an operator manually stopped it "
                   "(within 35 min of the delayed-stop error) to fix it. Recovery = minutes from STOPPED to RUNNING (median).",
        "time_title": "Time to Recover - Manual/Delayed Stop",
        "count_title": "Recovery Events - Manual/Delayed Stop",
    },
}


def _render_recovery_metric(metric_choice, date_from_str, date_to_str, aggregation):
    meta = _RECOVERY_META[metric_choice]
    with st.spinner("Loading recovery (event-log) data — this is heavier, please wait..."):
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
                "recover_manual": "Time to Recover – Manual/Delayed Stop (min)",
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
