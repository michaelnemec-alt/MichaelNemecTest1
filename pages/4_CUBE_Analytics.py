import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta

from cubeanalytics_utils import (
    is_api_configured, get_installations,
    query_system_health, query_uptime, query_robot_state, query_bin_presentations,
    query_port_wait_time_daily, query_port_uptime, query_incidents,
)

st.set_page_config(page_title="CUBE Analytics", page_icon="📊", layout="wide")

st.markdown(
    "<style>"
    "section[data-testid='stSidebar'] button {font-size: 10px !important; padding: 4px 8px !important; min-height: 0 !important;}"
    "</style>",
    unsafe_allow_html=True,
)

st.title("📊 CUBE Analytics")

if not is_api_configured():
    st.warning("CubeAnalytics API token not configured. Add `[cubeanalytics] token` to Streamlit secrets.")
    st.stop()

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    if "cube_date_range" not in st.session_state:
        st.session_state.cube_date_range = (date.today() - timedelta(days=30), date.today())

    st.caption(f"Today: **{date.today().strftime('%b %d, %Y')}**")
    date_val = st.date_input(
        "Select date range",
        value=st.session_state.cube_date_range,
        max_value=date.today(),
        key="cube_date_input",
    )
    if isinstance(date_val, tuple) and len(date_val) == 2:
        dt_from, dt_to = date_val
        st.session_state.cube_date_range = date_val
    elif isinstance(date_val, tuple) and len(date_val) == 1:
        dt_from, dt_to = date_val[0], None
    else:
        dt_from, dt_to = date_val, None

    preset_defs = [
        ("Yesterday", 1, 1),
        ("7 days", 7, 0),
        ("14 days", 14, 0),
        ("30 days", 30, 0),
        ("60 days", 60, 0),
        ("90 days", 90, 0),
    ]
    for row_presets in [preset_defs[:3], preset_defs[3:]]:
        cols = st.columns(3)
        for col, (label, days_back, end_offset) in zip(cols, row_presets):
            with col:
                if st.button(label, key=f"cube_preset_{days_back}", use_container_width=True):
                    st.session_state.cube_date_range = (
                        date.today() - timedelta(days=days_back),
                        date.today() - timedelta(days=end_offset),
                    )
                    st.rerun()

    st.divider()
    aggregation = st.radio("Aggregation", ["Day", "Week", "Month"], index=1, horizontal=True)

if not dt_from or not dt_to:
    st.info("Select both start and end dates.")
    st.stop()
if dt_from > dt_to:
    st.error("'From' date must be before 'To' date.")
    st.stop()


# ── Color palette matching Power BI style ────────────────────────────────────
SITE_COLORS = [
    "#5B9BD5", "#C5B200", "#8B4C6A", "#1F3864", "#7F7F7F",
    "#6B8E5A", "#2E2E2E", "#A0522D", "#4E8C3F", "#D35400",
]


def make_trend_chart(pivot_df, title, ylabel, threshold=None, threshold_label=None, pct=False):
    fig = go.Figure()
    sites = pivot_df.columns.tolist()
    for i, site in enumerate(sites):
        color = SITE_COLORS[i % len(SITE_COLORS)]
        vals = pivot_df[site]
        if pct:
            hover_text = [f"{site}<br>{pivot_df.index[j]}<br>{v:.2f}%" if pd.notna(v) else "" for j, v in enumerate(vals)]
        else:
            hover_text = [f"{site}<br>{pivot_df.index[j]}<br>{v:.2f}" if pd.notna(v) else "" for j, v in enumerate(vals)]
        fig.add_trace(go.Scatter(
            x=pivot_df.index, y=vals,
            mode="lines+markers",
            name=site,
            line=dict(color=color, width=2),
            marker=dict(size=4),
            hovertext=hover_text,
            hoverinfo="text",
        ))

    if threshold is not None:
        fig.add_hline(
            y=threshold, line_dash="dash", line_color="#7ab648", line_width=2,
            annotation_text=threshold_label or "Threshold",
            annotation_position="top left",
            annotation_font_color="#7ab648",
        )

    tick_fmt = ".1f%" if pct else ".2f"
    fig.update_layout(
        title=dict(text=title, font=dict(size=14, color="#1F3864"), x=0),
        yaxis_title=ylabel,
        xaxis_tickangle=-45,
        xaxis_tickfont=dict(size=9),
        yaxis_tickfont=dict(size=10),
        yaxis_tickformat=tick_fmt if pct else None,
        legend=dict(
            font=dict(size=9),
            orientation="v",
            yanchor="top", y=1, xanchor="left", x=1.02,
        ),
        height=380,
        margin=dict(l=60, r=200, t=40, b=60),
        hovermode="x unified",
        plot_bgcolor="white",
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#eee", gridwidth=1)
    return fig


def aggregate_pivot(df, value_col, agg_mode):
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


# ── Load all data ────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_all_data(date_from_str, date_to_str):
    installations = get_installations()
    health_frames, uptime_frames, robot_frames, bp_frames, pwt_frames = [], [], [], [], []
    port_up_frames, incident_frames = [], []

    for inst in installations:
        iid, name = inst["id"], inst["name"]

        df_h = query_system_health(iid, date_from_str, date_to_str)
        if not df_h.empty:
            df_h["site"] = name
            health_frames.append(df_h)

        df_u = query_uptime(iid, date_from_str, date_to_str)
        if not df_u.empty:
            df_u["site"] = name
            uptime_frames.append(df_u)

        df_r = query_robot_state(iid, date_from_str, date_to_str)
        if not df_r.empty:
            df_r["site"] = name
            robot_frames.append(df_r)

        df_bp = query_bin_presentations(iid, date_from_str, date_to_str)
        if not df_bp.empty:
            df_bp["site"] = name
            bp_frames.append(df_bp)

        df_pwt = query_port_wait_time_daily(iid, date_from_str, date_to_str)
        if not df_pwt.empty:
            df_pwt["site"] = name
            pwt_frames.append(df_pwt)

        df_pu = query_port_uptime(iid, date_from_str, date_to_str)
        if not df_pu.empty:
            df_pu["site"] = name
            port_up_frames.append(df_pu)

        df_inc = query_incidents(iid, date_from_str, date_to_str)
        if not df_inc.empty:
            df_inc["site"] = name
            incident_frames.append(df_inc)

    return {
        "health": pd.concat(health_frames, ignore_index=True) if health_frames else pd.DataFrame(),
        "uptime": pd.concat(uptime_frames, ignore_index=True) if uptime_frames else pd.DataFrame(),
        "robot": pd.concat(robot_frames, ignore_index=True) if robot_frames else pd.DataFrame(),
        "bp": pd.concat(bp_frames, ignore_index=True) if bp_frames else pd.DataFrame(),
        "pwt": pd.concat(pwt_frames, ignore_index=True) if pwt_frames else pd.DataFrame(),
        "port_uptime": pd.concat(port_up_frames, ignore_index=True) if port_up_frames else pd.DataFrame(),
        "incidents": pd.concat(incident_frames, ignore_index=True) if incident_frames else pd.DataFrame(),
    }


with st.spinner("Loading data from CubeAnalytics API for all sites..."):
    data = load_all_data(str(dt_from), str(dt_to))

df_health = data["health"]
df_uptime = data["uptime"]
df_robot = data["robot"]
df_bp = data["bp"]
df_pwt = data["pwt"]
df_port_uptime = data["port_uptime"]
df_incidents = data["incidents"]

if df_health.empty:
    st.warning("No data returned for the selected date range.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: CURRENT HEALTH OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
st.header("Current Health — All Sites")

latest_date = df_health["date"].max()
latest = df_health[df_health["date"] == latest_date][[
    "site", "health_index", "uptime", "wait_bin", "waste_time",
    "average_battery_score", "mtbf_h", "packet_loss", "mbbd",
]].copy()
latest.columns = ["Site", "Health", "Uptime %", "Wait (s)", "Waste (s)", "Battery", "MTBF (h)", "Pkt Loss %", "MBBD"]
latest = latest.sort_values("Health", ascending=False).reset_index(drop=True)


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


styled = latest.style.applymap(_color_health, subset=["Health"]).applymap(_color_uptime, subset=["Uptime %"])
st.dataframe(styled, use_container_width=True, hide_index=True)
st.caption(f"Data from: {latest_date.strftime('%Y-%m-%d')}")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: ERROR & HEALTH METRICS
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.header("Error & Health Metrics")

if not df_uptime.empty:
    df_uptime["system_uptime_pct"] = df_uptime["recovery_up_ratio"] * 100
    pivot = aggregate_pivot(df_uptime, "system_uptime_pct", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "System Uptime", "Uptime", threshold=99.7, threshold_label="Target 99.7%", pct=True), use_container_width=True)

if not df_uptime.empty:
    df_uptime["system_availability_pct"] = df_uptime["up_ratio"] * 100
    pivot = aggregate_pivot(df_uptime, "system_availability_pct", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "System Availability", "Availability", pct=True), use_container_width=True)

if not df_robot.empty:
    pivot = aggregate_pivot(df_robot, "robot_availability_pct", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Robot Availability", "% Available", pct=True), use_container_width=True)

# Port Uptime
if not df_port_uptime.empty:
    pivot = aggregate_pivot(df_port_uptime, "uptime_pct", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Port Uptime", "Uptime %", pct=True), use_container_width=True)

# Incident Count
if not df_incidents.empty:
    pivot = aggregate_pivot(df_incidents, "incident_count", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Incident Count", "Count"), use_container_width=True)

if not df_health.empty and "packet_loss" in df_health.columns:
    pivot = aggregate_pivot(df_health, "packet_loss", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Packet Loss", "Packet Loss %", threshold=5.0, threshold_label="Target < 5%", pct=True), use_container_width=True)

if not df_health.empty and "mtbf_h" in df_health.columns:
    pivot = aggregate_pivot(df_health, "mtbf_h", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "MTBF (Mean Time Between Failures)", "Hours"), use_container_width=True)

if not df_health.empty and "mbbd" in df_health.columns:
    pivot = aggregate_pivot(df_health, "mbbd", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "MBBD (Mean Bins Between Downtime)", "Bins"), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.header("Performance")

if not df_health.empty and "wait_bin" in df_health.columns:
    pivot = aggregate_pivot(df_health, "wait_bin", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Wait Time (system-level)", "Wait Time (s)", threshold=2.0, threshold_label="Target < 2s"), use_container_width=True)

if not df_health.empty and "waste_time" in df_health.columns:
    pivot = aggregate_pivot(df_health, "waste_time", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Waste Time (system-level)", "Waste Time (s)", threshold=0.5, threshold_label="Target < 0.5s"), use_container_width=True)

st.subheader("Filtered by Pick Type / Category")

if not df_pwt.empty:
    filter_col1, filter_col2 = st.columns(2)

    all_pick_types = sorted(df_pwt["pick_type"].dropna().unique().tolist())
    all_categories = sorted(df_pwt["category"].dropna().unique().tolist())
    all_categories = [c for c in all_categories if c != ""]

    with filter_col1:
        selected_pick_types = st.multiselect(
            "Pick type", all_pick_types, default=all_pick_types, key="perf_pick_type",
        )
    with filter_col2:
        selected_categories = st.multiselect(
            "Category", all_categories, default=all_categories, key="perf_category",
        )

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

        def _format_index(pivot, mode):
            if mode == "Week":
                pivot.index = pivot.index.strftime("W%V %Y")
            elif mode == "Month":
                pivot.index = pivot.index.strftime("%Y-%m")
            else:
                pivot.index = pivot.index.strftime("%Y-%m-%d")
            return pivot

        for metric, label, ylabel, thresh, thresh_label in [
            ("avg_wait_bin", "Bin Wait Time (filtered)", "Wait Bin (s)", 2.0, "Target < 2s"),
            ("avg_wait_user", "User Wait Time (filtered)", "Wait User (s)", None, None),
            ("avg_waste", "Waste Time (filtered)", "Waste Time (s)", 0.5, "Target < 0.5s"),
        ]:
            piv = agg.pivot(index="period", columns="site", values=metric).sort_index()
            piv = _format_index(piv, aggregation)
            st.plotly_chart(make_trend_chart(piv, label, ylabel, threshold=thresh, threshold_label=thresh_label), use_container_width=True)

        piv_count = agg.pivot(index="period", columns="site", values="total_count").sort_index()
        piv_count = _format_index(piv_count, aggregation)
        st.plotly_chart(make_trend_chart(piv_count, "Bin Presentations (filtered)", "Count"), use_container_width=True)
else:
    st.info("No port wait time data available.")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: BATTERY & ROBOTS
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.header("Battery & Robots")

if not df_health.empty and "average_battery_score" in df_health.columns:
    pivot = aggregate_pivot(df_health, "average_battery_score", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Average Battery Score", "Score (1-5)"), use_container_width=True)

if not df_robot.empty and "working_pct" in df_robot.columns:
    pivot = aggregate_pivot(df_robot, "working_pct", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Robot Working %", "% Working", pct=True), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: HEALTH INDEX TREND
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.header("Health Index")

if not df_health.empty and "health_index" in df_health.columns:
    pivot = aggregate_pivot(df_health, "health_index", aggregation)
    st.plotly_chart(make_trend_chart(pivot, "Health Index", "Index (1-5)", threshold=4.0, threshold_label="Target >= 4.0"), use_container_width=True)


# ── Download ─────────────────────────────────────────────────────────────────
st.divider()
all_frames = []
for key, df in data.items():
    if not df.empty:
        df_copy = df.copy()
        df_copy["source"] = key
        all_frames.append(df_copy)
if all_frames:
    export_df = pd.concat(all_frames, ignore_index=True)
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download all data as CSV",
        data=csv_bytes,
        file_name=f"cube_analytics_{dt_from}_{dt_to}.csv",
        mime="text/csv",
    )
