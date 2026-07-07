import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from datetime import date, timedelta

from cubeanalytics_utils import (
    is_api_configured, get_installations,
    query_system_health, query_uptime, query_robot_state, query_bin_presentations,
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
    fig, ax = plt.subplots(figsize=(12, 4))
    sites = pivot_df.columns.tolist()
    for i, site in enumerate(sites):
        color = SITE_COLORS[i % len(SITE_COLORS)]
        ax.plot(pivot_df.index, pivot_df[site], color=color, linewidth=1.2, label=site, marker="", markersize=3)

    if threshold is not None:
        ax.axhline(y=threshold, color="#7ab648", linestyle="--", linewidth=1.5, alpha=0.7, label=threshold_label or "Threshold")

    ax.set_title(title, fontsize=13, fontweight="bold", loc="left")
    ax.set_ylabel(ylabel, fontsize=10)
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.tick_params(axis="y", labelsize=9)
    if pct:
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.grid(axis="y", alpha=0.3)
    ax.legend(
        bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=7,
        frameon=True, framealpha=0.9, edgecolor="#ccc",
    )
    fig.tight_layout()
    return fig


def aggregate_pivot(df, value_col, agg_mode):
    if agg_mode == "Week":
        df = df.copy()
        df["period"] = df["date"].dt.to_period("W").dt.start_time
    elif agg_mode == "Month":
        df = df.copy()
        df["period"] = df["date"].dt.to_period("M").dt.start_time
    else:
        df = df.copy()
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
    health_frames, uptime_frames, robot_frames, bp_frames = [], [], [], []

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

    return {
        "health": pd.concat(health_frames, ignore_index=True) if health_frames else pd.DataFrame(),
        "uptime": pd.concat(uptime_frames, ignore_index=True) if uptime_frames else pd.DataFrame(),
        "robot": pd.concat(robot_frames, ignore_index=True) if robot_frames else pd.DataFrame(),
        "bp": pd.concat(bp_frames, ignore_index=True) if bp_frames else pd.DataFrame(),
    }


with st.spinner("Loading data from CubeAnalytics API for all sites..."):
    data = load_all_data(str(dt_from), str(dt_to))

df_health = data["health"]
df_uptime = data["uptime"]
df_robot = data["robot"]
df_bp = data["bp"]

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

# System Uptime (recovery_up_ratio from uptime endpoint)
if not df_uptime.empty:
    df_uptime["system_uptime_pct"] = df_uptime["recovery_up_ratio"] * 100
    pivot = aggregate_pivot(df_uptime, "system_uptime_pct", aggregation)
    fig = make_trend_chart(pivot, "System Uptime", "Uptime", threshold=99.7, threshold_label="Target 99.7%", pct=True)
    st.pyplot(fig)
    plt.close(fig)

# System Availability (up_ratio from uptime endpoint)
if not df_uptime.empty:
    df_uptime["system_availability_pct"] = df_uptime["up_ratio"] * 100
    pivot = aggregate_pivot(df_uptime, "system_availability_pct", aggregation)
    fig = make_trend_chart(pivot, "System Availability", "Availability", pct=True)
    st.pyplot(fig)
    plt.close(fig)

# Robot Availability
if not df_robot.empty:
    pivot = aggregate_pivot(df_robot, "robot_availability_pct", aggregation)
    fig = make_trend_chart(pivot, "Robot Availability", "% Available", pct=True)
    st.pyplot(fig)
    plt.close(fig)

# Packet Loss
if not df_health.empty and "packet_loss" in df_health.columns:
    pivot = aggregate_pivot(df_health, "packet_loss", aggregation)
    fig = make_trend_chart(pivot, "Packet Loss", "Packet Loss %", threshold=5.0, threshold_label="Target < 5%", pct=True)
    st.pyplot(fig)
    plt.close(fig)

# MTBF
if not df_health.empty and "mtbf_h" in df_health.columns:
    pivot = aggregate_pivot(df_health, "mtbf_h", aggregation)
    fig = make_trend_chart(pivot, "MTBF (Mean Time Between Failures)", "Hours")
    st.pyplot(fig)
    plt.close(fig)

# MBBD
if not df_health.empty and "mbbd" in df_health.columns:
    pivot = aggregate_pivot(df_health, "mbbd", aggregation)
    fig = make_trend_chart(pivot, "MBBD (Mean Bins Between Downtime)", "Bins")
    st.pyplot(fig)
    plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.header("Performance")

# Wait Time (bin)
if not df_health.empty and "wait_bin" in df_health.columns:
    pivot = aggregate_pivot(df_health, "wait_bin", aggregation)
    fig = make_trend_chart(pivot, "Wait Time", "Wait Time (s)", threshold=2.0, threshold_label="Target < 2s")
    st.pyplot(fig)
    plt.close(fig)

# Waste Time
if not df_health.empty and "waste_time" in df_health.columns:
    pivot = aggregate_pivot(df_health, "waste_time", aggregation)
    fig = make_trend_chart(pivot, "Waste Time", "Waste Time (s)", threshold=0.5, threshold_label="Target < 0.5s")
    st.pyplot(fig)
    plt.close(fig)

# Wait User (from bin presentations)
if not df_bp.empty and "avg_wait_user" in df_bp.columns:
    pivot = aggregate_pivot(df_bp, "avg_wait_user", aggregation)
    fig = make_trend_chart(pivot, "Wait User", "Wait User (s)")
    st.pyplot(fig)
    plt.close(fig)

# Bin Presentations count
if not df_bp.empty and "bin_presentations" in df_bp.columns:
    pivot = aggregate_pivot(df_bp, "bin_presentations", aggregation)
    fig = make_trend_chart(pivot, "Bin Presentations", "Count")
    st.pyplot(fig)
    plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: BATTERY & ROBOTS
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.header("Battery & Robots")

# Battery Score
if not df_health.empty and "average_battery_score" in df_health.columns:
    pivot = aggregate_pivot(df_health, "average_battery_score", aggregation)
    fig = make_trend_chart(pivot, "Average Battery Score", "Score (1-5)")
    st.pyplot(fig)
    plt.close(fig)

# Robot Working %
if not df_robot.empty and "working_pct" in df_robot.columns:
    pivot = aggregate_pivot(df_robot, "working_pct", aggregation)
    fig = make_trend_chart(pivot, "Robot Working %", "% Working", pct=True)
    st.pyplot(fig)
    plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: HEALTH INDEX TREND
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.header("Health Index")

if not df_health.empty and "health_index" in df_health.columns:
    pivot = aggregate_pivot(df_health, "health_index", aggregation)
    fig = make_trend_chart(pivot, "Health Index", "Index (1-5)", threshold=4.0, threshold_label="Target ≥ 4.0")
    st.pyplot(fig)
    plt.close(fig)


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
