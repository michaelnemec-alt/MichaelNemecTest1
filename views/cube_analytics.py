import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta

from cubeanalytics_utils import (
    is_api_configured, get_installations,
    query_system_health, query_uptime, query_robot_state, query_bin_presentations,
    query_port_wait_time_daily, query_port_uptime, query_incidents,
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
    "Health Index",
]


def _make_trend_chart(pivot_df, title, ylabel, threshold=None, threshold_label=None, pct=False):
    fig = go.Figure()
    sites = pivot_df.columns.tolist()
    site_means = {s: pivot_df[s].mean() for s in sites}
    sorted_sites = sorted(sites, key=lambda s: site_means.get(s, 0), reverse=True)
    color_map = {site: SITE_COLORS[i % len(SITE_COLORS)] for i, site in enumerate(sites)}
    for site in sorted_sites:
        color = color_map[site]
        vals = pivot_df[site]
        short_name = site.split("-", 1)[-1] if "-" in site else site
        hover_fmt = "%{y:.2f}%" if pct else "%{y:.2f}"
        fig.add_trace(go.Scatter(
            x=pivot_df.index, y=vals,
            mode="lines+markers", name=short_name,
            line=dict(color=color, width=2),
            marker=dict(size=4),
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
        title=dict(text=title, font=dict(size=14, color="#1F3864"), x=0),
        yaxis_title=ylabel,
        xaxis_tickangle=-45,
        xaxis_tickfont=dict(size=9),
        yaxis_tickfont=dict(size=10),
        yaxis_tickformat=".1f%" if pct else None,
        legend=dict(font=dict(size=9), orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
        height=380,
        margin=dict(l=60, r=200, t=40, b=60),
        hovermode="x unified",
        hoverlabel=dict(font_size=11),
        plot_bgcolor="white",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#eee", gridwidth=1)
    return fig


def _aggregate_pivot(df, value_col, agg_mode):
    df = df.copy()
    if agg_mode == "Week":
        df["period"] = df["date"].dt.to_period("W").dt.start_time
    elif agg_mode == "Month":
        df["period"] = df["date"].dt.to_period("M").dt.start_time
    else:
        df["period"] = df["date"]
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
    if mode == "Week":
        pivot.index = pivot.index.strftime("W%V %Y")
    elif mode == "Month":
        pivot.index = pivot.index.strftime("%Y-%m")
    else:
        pivot.index = pivot.index.strftime("%Y-%m-%d")
    return pivot


def _load_for_sites(query_fn, date_from_str, date_to_str):
    installations = get_installations()
    frames = []
    for inst in installations:
        df = query_fn(inst["id"], date_from_str, date_to_str)
        if not df.empty:
            df["site"] = inst["name"]
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def render():
    if not is_api_configured():
        st.warning("CubeAnalytics API token not configured. Add `[cubeanalytics] token` to Streamlit secrets.")
        return

    with st.sidebar:
        st.markdown("#### CUBE Analytics")

        selected_view = st.selectbox("Dashboard view", VIEWS, index=0, key="cube_view")
        st.divider()

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
            st.session_state.cube_custom_range = date_val
        else:
            dt_from, dt_to = (date_val[0] if isinstance(date_val, tuple) else date_val), None

        st.divider()
        aggregation = st.radio("Aggregation", ["Day", "Week", "Month"], index=1, horizontal=True, key="cube_agg")

    if not dt_from or not dt_to:
        st.info("Select both start and end dates.")
        return
    if dt_from > dt_to:
        st.error("'From' date must be before 'To' date.")
        return

    date_from_str = str(dt_from)
    date_to_str = str(dt_to)

    if selected_view == "Overview & Health":
        _view_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to)
    elif selected_view == "Error & Health Metrics":
        _view_error_health(date_from_str, date_to_str, aggregation)
    elif selected_view == "Performance":
        _view_performance(date_from_str, date_to_str, aggregation)
    elif selected_view == "Battery & Robots":
        _view_battery_robots(date_from_str, date_to_str, aggregation)
    elif selected_view == "Health Index":
        _view_health_index(date_from_str, date_to_str, aggregation)


def _view_overview(date_from_str, date_to_str, aggregation, dt_from, dt_to):
    with st.spinner("Loading system health..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)

    if df_health.empty:
        st.warning("No data returned for the selected date range.")
        return

    st.markdown("#### Current Health — All Sites")
    latest_date = df_health["date"].max()
    latest = df_health[df_health["date"] == latest_date][[
        "site", "health_index", "uptime", "wait_bin", "waste_time",
        "average_battery_score", "mtbf_h", "packet_loss", "mbbd",
    ]].copy()
    latest.columns = ["Site", "Health", "Uptime %", "Wait (s)", "Waste (s)", "Battery", "MTBF (h)", "Pkt Loss %", "MBBD"]
    latest = latest.sort_values("Health", ascending=False).reset_index(drop=True)

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
        .applymap(_color_health, subset=["Health"])
        .applymap(_color_uptime, subset=["Uptime %"])
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
    st.caption(f"Data from: {latest_date.strftime('%Y-%m-%d')}")

    st.divider()
    col_title, col_info = st.columns([8, 1])
    with col_title:
        st.markdown("#### Health Index Trend")
    with col_info:
        with st.popover("..."):
            st.markdown("""**Health Index (1-5 scale)**

Composite score of 7 component scores:
- **5.0** = Excellent — all metrics optimal
- **4.0-4.9** = Good — target range
- **3.0-3.9** = Room for improvement
- **< 3.0** = Needs attention

**Components:** Uptime, Wait Time, Waste Time, Battery, MTBF, Packet Loss, MBBD

Each component scores 1-3 (Good / Room for Improvement). The health index is a weighted composite rounded to 2 decimals.

The dashed green line at 4.0 = target threshold.""")

    pivot = _aggregate_pivot(df_health, "health_index", aggregation)
    st.plotly_chart(_make_trend_chart(pivot, "Health Index", "Index (1-5)", threshold=4.0, threshold_label="Target >= 4.0"), use_container_width=True)

    st.divider()
    csv_bytes = df_health.to_csv(index=False).encode("utf-8")
    st.download_button("Download health data", data=csv_bytes, file_name=f"health_{dt_from}_{dt_to}.csv", mime="text/csv", key="dl_health")


def _view_error_health(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading uptime, robot state, and health data..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)
        df_uptime = _load_for_sites(query_uptime, date_from_str, date_to_str)
        df_robot = _load_for_sites(query_robot_state, date_from_str, date_to_str)
        df_port_uptime = _load_for_sites(query_port_uptime, date_from_str, date_to_str)
        df_incidents = _load_for_sites(query_incidents, date_from_str, date_to_str)

    if df_health.empty:
        st.warning("No data returned.")
        return

    st.markdown("#### Error & Health Metrics")

    if not df_uptime.empty:
        df_uptime["system_uptime_pct"] = df_uptime["recovery_up_ratio"] * 100
        pivot = _aggregate_pivot(df_uptime, "system_uptime_pct", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "System Uptime", "Uptime", threshold=99.7, threshold_label="Target 99.7%", pct=True), use_container_width=True)

    if not df_uptime.empty:
        df_uptime["system_availability_pct"] = df_uptime["up_ratio"] * 100
        pivot = _aggregate_pivot(df_uptime, "system_availability_pct", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "System Availability", "Availability", pct=True), use_container_width=True)

    if not df_robot.empty:
        pivot = _aggregate_pivot(df_robot, "robot_availability_pct", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Robot Availability", "% Available", pct=True), use_container_width=True)

    if not df_port_uptime.empty:
        pivot = _aggregate_pivot(df_port_uptime, "uptime_pct", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Port Uptime", "Uptime %", pct=True), use_container_width=True)

    if not df_incidents.empty:
        pivot = _aggregate_pivot(df_incidents, "incident_count", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Incident Count", "Count"), use_container_width=True)

    if "packet_loss" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "packet_loss", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Packet Loss", "Packet Loss %", threshold=5.0, threshold_label="Target < 5%", pct=True), use_container_width=True)

    if "mtbf_h" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "mtbf_h", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "MTBF (Mean Time Between Failures)", "Hours"), use_container_width=True)

    if "mbbd" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "mbbd", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "MBBD (Mean Bins Between Downtime)", "Bins"), use_container_width=True)


def _view_performance(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading performance data..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)
        df_pwt = _load_for_sites(query_port_wait_time_daily, date_from_str, date_to_str)

    if df_health.empty:
        st.warning("No data returned.")
        return

    st.markdown("#### Performance")

    if "wait_bin" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "wait_bin", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Wait Time (system-level)", "Wait Time (s)", threshold=2.0, threshold_label="Target < 2s"), use_container_width=True)

    if "waste_time" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "waste_time", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Waste Time (system-level)", "Waste Time (s)", threshold=0.5, threshold_label="Target < 0.5s"), use_container_width=True)

    st.divider()
    st.markdown("**Filtered by Pick Type / Category**")

    if not df_pwt.empty:
        filter_col1, filter_col2 = st.columns(2)

        all_pick_types = sorted(df_pwt["pick_type"].dropna().unique().tolist())
        all_categories = sorted(c for c in df_pwt["category"].dropna().unique().tolist() if c != "")

        with filter_col1:
            selected_pick_types = st.multiselect("Pick type", all_pick_types, default=all_pick_types, key="perf_pick_type")
        with filter_col2:
            selected_categories = st.multiselect("Category", all_categories, default=all_categories, key="perf_category")

        df_filtered = df_pwt.copy()
        if selected_pick_types:
            df_filtered = df_filtered[df_filtered["pick_type"].isin(selected_pick_types)]
        if selected_categories:
            df_filtered = df_filtered[df_filtered["category"].isin(selected_categories)]

        if df_filtered.empty:
            st.info("No data for the selected filters.")
        else:
            wt = df_filtered.copy()
            wt["w_wait_bin"] = wt["average_wait_bin"] * wt["count"]
            wt["w_wait_user"] = wt["average_wait_user"] * wt["count"]
            wt["w_waste"] = wt["average_waste_time"] * wt["count"]

            if aggregation == "Week":
                wt["period"] = wt["date"].dt.to_period("W").dt.start_time
            elif aggregation == "Month":
                wt["period"] = wt["date"].dt.to_period("M").dt.start_time
            else:
                wt["period"] = wt["date"]

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
                piv = _format_pivot_index(piv, aggregation)
                st.plotly_chart(_make_trend_chart(piv, label, ylabel, threshold=thresh, threshold_label=thresh_label), use_container_width=True)

            piv_count = agg.pivot(index="period", columns="site", values="total_count").sort_index()
            piv_count = _format_pivot_index(piv_count, aggregation)
            st.plotly_chart(_make_trend_chart(piv_count, "Bin Presentations (filtered)", "Count"), use_container_width=True)
    else:
        st.info("No port wait time data available.")


def _view_battery_robots(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading battery and robot data..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)
        df_robot = _load_for_sites(query_robot_state, date_from_str, date_to_str)

    if df_health.empty:
        st.warning("No data returned.")
        return

    st.markdown("#### Battery & Robots")

    if "average_battery_score" in df_health.columns:
        pivot = _aggregate_pivot(df_health, "average_battery_score", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Average Battery Score", "Score (1-5)"), use_container_width=True)

    if not df_robot.empty and "working_pct" in df_robot.columns:
        pivot = _aggregate_pivot(df_robot, "working_pct", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Robot Working %", "% Working", pct=True), use_container_width=True)

    if not df_robot.empty and "robot_availability_pct" in df_robot.columns:
        pivot = _aggregate_pivot(df_robot, "robot_availability_pct", aggregation)
        st.plotly_chart(_make_trend_chart(pivot, "Robot Availability", "% Available", pct=True), use_container_width=True)


def _view_health_index(date_from_str, date_to_str, aggregation):
    with st.spinner("Loading health index data..."):
        df_health = _load_for_sites(query_system_health, date_from_str, date_to_str)

    if df_health.empty:
        st.warning("No data returned.")
        return

    st.markdown("#### Health Index")

    pivot = _aggregate_pivot(df_health, "health_index", aggregation)
    st.plotly_chart(_make_trend_chart(pivot, "Health Index", "Index (1-5)", threshold=4.0, threshold_label="Target >= 4.0"), use_container_width=True)

    st.divider()
    metric_choice = st.selectbox(
        "Explore other metric",
        ["uptime", "wait_bin", "waste_time", "average_battery_score", "packet_loss", "mtbf_h", "mbbd"],
        format_func=lambda k: {
            "uptime": "Uptime %", "wait_bin": "Wait Bin (s)", "waste_time": "Waste Time (s)",
            "average_battery_score": "Battery Score", "packet_loss": "Packet Loss %",
            "mtbf_h": "MTBF (h)", "mbbd": "MBBD",
        }.get(k, k),
        key="cube_explore_metric",
    )
    if metric_choice in df_health.columns:
        pct = metric_choice in ("uptime", "packet_loss")
        pivot = _aggregate_pivot(df_health, metric_choice, aggregation)
        st.plotly_chart(_make_trend_chart(pivot, metric_choice.replace("_", " ").title(), metric_choice, pct=pct), use_container_width=True)
