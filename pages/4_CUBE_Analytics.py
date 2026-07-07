import streamlit as st
import pandas as pd
from datetime import date, timedelta

from cubeanalytics_utils import is_api_configured, get_installations, query_system_health

st.set_page_config(page_title="CUBE Analytics", page_icon="📊", layout="wide")

st.markdown(
    "<style>"
    "section[data-testid='stSidebar'] button {font-size: 10px !important; padding: 4px 8px !important; min-height: 0 !important;}"
    "</style>",
    unsafe_allow_html=True,
)

st.title("📊 CUBE Analytics — System Health")

if not is_api_configured():
    st.warning("CubeAnalytics API token not configured. Add `[cubeanalytics] token` to Streamlit secrets.")
    st.stop()

HEALTH_COLS = {
    "health_index": "Health Index",
    "uptime": "Uptime %",
    "wait_bin": "Wait Bin (s)",
    "waste_time": "Waste Time (s)",
    "average_battery_score": "Battery",
    "mtbf_h": "MTBF (h)",
    "packet_loss": "Pkt Loss %",
    "mbbd": "MBBD",
}

SCORE_COLS = {
    "uptime_score": "Uptime",
    "wait_time_score": "Wait Time",
    "waste_time_score": "Waste Time",
    "battery_score": "Battery",
    "mtbf_score": "MTBF",
    "packet_loss_score": "Pkt Loss",
    "mbbd_score": "MBBD",
}

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    if "cube_date_range" not in st.session_state:
        st.session_state.cube_date_range = (
            date.today() - timedelta(days=7), date.today(),
        )

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
    aggregation = st.radio("Aggregation", ["Day", "Week", "Month"], index=0, horizontal=True)

if not dt_from or not dt_to:
    st.info("Select both start and end dates.")
    st.stop()

if dt_from > dt_to:
    st.error("'From' date must be before 'To' date.")
    st.stop()


# ── Load data for all installations ─────────────────────────────────────────
@st.cache_data(ttl=300)
def load_all_health(date_from_str, date_to_str):
    installations = get_installations()
    frames = []
    for inst in installations:
        df = query_system_health(inst["id"], date_from_str, date_to_str)
        if df.empty:
            continue
        df["site"] = inst["name"]
        df["city"] = inst["city"]
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


with st.spinner("Loading system health data for all sites..."):
    df_all = load_all_health(str(dt_from), str(dt_to))

if df_all.empty:
    st.warning("No data returned for the selected date range.")
    st.stop()

# ── Aggregate ────────────────────────────────────────────────────────────────
if aggregation == "Week":
    df_all["period"] = df_all["date"].dt.to_period("W").apply(lambda p: p.start_time)
elif aggregation == "Month":
    df_all["period"] = df_all["date"].dt.to_period("M").apply(lambda p: p.start_time)
else:
    df_all["period"] = df_all["date"]

numeric_cols = list(HEALTH_COLS.keys())
agg_df = df_all.groupby(["site", "period"])[numeric_cols].mean().reset_index()
for c in ["health_index", "uptime", "wait_bin", "waste_time", "average_battery_score", "packet_loss"]:
    if c in agg_df.columns:
        agg_df[c] = agg_df[c].round(2)
for c in ["mtbf_h", "mbbd"]:
    if c in agg_df.columns:
        agg_df[c] = agg_df[c].round(0)

# ── Latest snapshot table ────────────────────────────────────────────────────
st.subheader("Current Health — All Sites")

latest_date = df_all["date"].max()
latest = df_all[df_all["date"] == latest_date][["site"] + list(HEALTH_COLS.keys())].copy()
latest = latest.rename(columns=HEALTH_COLS)
latest = latest.sort_values("Health Index", ascending=False).reset_index(drop=True)

def color_health(val):
    if pd.isna(val):
        return ""
    if val >= 4.5:
        return "background-color: #c6efce"
    if val >= 3.5:
        return "background-color: #ffeb9c"
    return "background-color: #ffc7ce"

def color_uptime(val):
    if pd.isna(val):
        return ""
    if val >= 99.9:
        return "background-color: #c6efce"
    if val >= 99.0:
        return "background-color: #ffeb9c"
    return "background-color: #ffc7ce"

styled = latest.style.applymap(color_health, subset=["Health Index"]).applymap(color_uptime, subset=["Uptime %"])
st.dataframe(styled, use_container_width=True, hide_index=True)
st.caption(f"Data from: {latest_date.strftime('%Y-%m-%d')}")

# ── Trend charts ─────────────────────────────────────────────────────────────
st.divider()
st.subheader(f"Trends ({aggregation})")

metric_choice = st.selectbox(
    "Metric",
    list(HEALTH_COLS.keys()),
    format_func=lambda k: HEALTH_COLS[k],
)

pivot = agg_df.pivot(index="period", columns="site", values=metric_choice).sort_index()
st.line_chart(pivot, use_container_width=True)

# ── Per-site detail ──────────────────────────────────────────────────────────
st.divider()
st.subheader("Site Detail")

sites = sorted(df_all["site"].unique())
selected_site = st.selectbox("Select site", sites)

site_df = agg_df[agg_df["site"] == selected_site].sort_values("period")

if not site_df.empty:
    metric_cols_present = [c for c in HEALTH_COLS if c in site_df.columns]
    display_df = site_df[["period"] + metric_cols_present].rename(
        columns={**{"period": aggregation}, **HEALTH_COLS}
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        chart_data = site_df.set_index("period")[["health_index", "uptime"]].rename(
            columns={"health_index": "Health Index", "uptime": "Uptime %"}
        )
        st.line_chart(chart_data)
    with col2:
        chart_data2 = site_df.set_index("period")[["wait_bin", "waste_time"]].rename(
            columns={"wait_bin": "Wait Bin (s)", "waste_time": "Waste Time (s)"}
        )
        st.line_chart(chart_data2)

# ── Download ─────────────────────────────────────────────────────────────────
st.divider()
csv_bytes = agg_df.to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇️ Download all data as CSV",
    data=csv_bytes,
    file_name=f"cube_analytics_health_{dt_from}_{dt_to}.csv",
    mime="text/csv",
)
