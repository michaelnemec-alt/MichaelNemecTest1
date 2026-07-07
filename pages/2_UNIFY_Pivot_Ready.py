import streamlit as st
import pandas as pd
import re
import io
from datetime import date, timedelta

from cubeanalytics_utils import is_api_configured, get_installations, query_port_wait_time

st.set_page_config(
    page_title="UNIFY Pivot Ready",
    page_icon="🔄",
    layout="wide",
)

st.title("🔄 UNIFY Pivot Ready")
st.markdown(
    "Convert Unify Analytics / CubeAnalytics data into **pivot-ready long format** CSV."
)


def make_output_name(input_name):
    """Derive output filename: 'CubeAnalytics (9).csv' → 'CubeAnalytics_9_Pivot_Ready.csv'"""
    base = input_name.rsplit(".", 1)[0]
    clean = re.sub(r"\s*\((\d+)\)", r"_\1", base)
    clean = clean.replace(" ", "_")
    return f"{clean}_Pivot_Ready.csv"


def process_dataframe(df):
    """Process a DataFrame with the standard columns into pivot-ready format."""
    required = ["Timestamp", "Port ID", "Pick type", "Count",
                 "Average bin wait time", "Average operator handling time"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return None, f"Missing columns: {missing}"

    df = df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["Timestamp"])

    df["Date"] = df["Timestamp"].dt.date
    df["Hour"] = df["Timestamp"].dt.hour

    if "Category" not in df.columns:
        df["Category"] = ""
    df["Category"] = df["Category"].fillna("")
    df["Category"] = df["Category"].apply(
        lambda x: str(int(float(x))) if x != "" and str(x).replace(".", "", 1).isdigit() else str(x) if x != "" else ""
    )

    for col in ["Count", "Average bin wait time", "Average operator handling time"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["_w_bin_wait"] = df["Count"] * df["Average bin wait time"]
    df["_w_op_handling"] = df["Count"] * df["Average operator handling time"]

    group_cols = ["Date", "Hour", "Pick type", "Port ID", "Category"]
    agg = df.groupby(group_cols, dropna=False).agg(
        Count=("Count", "sum"),
        _w_bin_wait=("_w_bin_wait", "sum"),
        _w_op_handling=("_w_op_handling", "sum"),
    ).reset_index()

    agg = agg[agg["Count"] > 0].copy()

    agg["Average bin wait time"] = agg["_w_bin_wait"] / agg["Count"]
    agg["Average operator handling time"] = agg["_w_op_handling"] / agg["Count"]

    agg["Average open ports"] = (
        (agg["Average bin wait time"] + agg["Average operator handling time"])
        * agg["Count"] / 3600
    ).clip(upper=1)

    agg = agg.sort_values(["Date", "Hour", "Pick type", "Port ID", "Category"])

    result = agg[[
        "Date", "Hour", "Pick type", "Port ID", "Category",
        "Count", "Average bin wait time", "Average operator handling time",
        "Average open ports"
    ]].copy()
    result = result.rename(columns={"Port ID": "Port number"})

    for col in ["Average bin wait time", "Average operator handling time", "Average open ports"]:
        result[col] = result[col].round(4)
    result["Count"] = result["Count"].astype(int)

    return result, None


def process_file(raw_bytes, filename):
    """Process one CSV file per the UNIFY_PIVOT_READY_V13 manual."""
    df = pd.read_csv(io.BytesIO(raw_bytes))
    return process_dataframe(df)


# ── Sidebar ─────────────────────────────────────────────────────────────────
api_available = is_api_configured()

with st.sidebar:
    st.header("⚙️ Settings")

    if api_available:
        data_source = st.radio("Data source", ["CubeAnalytics API", "CSV Upload"], index=0)
    else:
        data_source = "CSV Upload"

    uploaded_files = None
    api_installation = None
    api_date_from = None
    api_date_to = None

    if data_source == "CubeAnalytics API":
        try:
            installations = get_installations()
        except Exception as e:
            st.error(f"Failed to fetch installations: {e}")
            installations = []

        if installations:
            install_labels = [f"{inst['name']} ({inst['city']})" for inst in installations]
            selected_idx = st.selectbox(
                "Installation", range(len(install_labels)),
                format_func=lambda i: install_labels[i],
            )
            api_installation = installations[selected_idx]

            if "api_date_range" not in st.session_state:
                st.session_state.api_date_range = (
                    date.today() - timedelta(days=7), date.today(),
                )

            st.caption(f"Today: **{date.today().strftime('%b %d, %Y')}**")
            date_val = st.date_input(
                "Select date range",
                value=st.session_state.api_date_range,
                max_value=date.today(),
            )
            if isinstance(date_val, tuple) and len(date_val) == 2:
                api_date_from, api_date_to = date_val
                st.session_state.api_date_range = date_val
            elif isinstance(date_val, tuple) and len(date_val) == 1:
                api_date_from = date_val[0]
                api_date_to = None
            else:
                api_date_from = date_val
                api_date_to = None

            preset_defs = [
                ("Yesterday", 1, 1),
                ("7 days", 7, 0),
                ("14 days", 14, 0),
                ("30 days", 30, 0),
                ("60 days", 60, 0),
                ("90 days", 90, 0),
            ]
            row1 = preset_defs[:3]
            row2 = preset_defs[3:]
            cols1 = st.columns(3)
            for col, (label, days_back, end_offset) in zip(cols1, row1):
                with col:
                    if st.button(label, key=f"preset_{days_back}", use_container_width=True):
                        st.session_state.api_date_range = (
                            date.today() - timedelta(days=days_back),
                            date.today() - timedelta(days=end_offset),
                        )
                        st.rerun()
            cols2 = st.columns(3)
            for col, (label, days_back, end_offset) in zip(cols2, row2):
                with col:
                    if st.button(label, key=f"preset_{days_back}", use_container_width=True):
                        st.session_state.api_date_range = (
                            date.today() - timedelta(days=days_back),
                            date.today() - timedelta(days=end_offset),
                        )
                        st.rerun()
    else:
        uploaded_files = st.file_uploader(
            "Upload CSV files",
            type=["csv"],
            accept_multiple_files=True,
            help="Unify Analytics / CubeAnalytics CSV exports. You can upload multiple files at once.",
        )
        st.divider()
        st.markdown(
            "**Required columns:**\n"
            "- `Timestamp`\n"
            "- `Port ID`\n"
            "- `Pick type`\n"
            "- `Count`\n"
            "- `Average bin wait time`\n"
            "- `Average operator handling time`\n"
            "\n*Optional:* `Category`"
        )


# ── Helper to render results ────────────────────────────────────────────────
def show_result(result, output_name, key_suffix):
    col1, col2, col3, col4 = st.columns(4)
    dates = result["Date"].unique()
    col1.metric("Rows", f"{len(result):,}")
    col2.metric("Days", len(dates))
    col3.metric("Ports", result["Port number"].nunique())
    col4.metric("Pick types", result["Pick type"].nunique())

    st.markdown("**Preview (first 20 rows):**")
    st.dataframe(result.head(20), use_container_width=True)

    csv_bytes = result.to_csv(index=False).encode("utf-8")
    st.download_button(
        f"⬇️ Download {output_name}",
        data=csv_bytes,
        file_name=output_name,
        mime="text/csv",
        key=f"download_{key_suffix}",
    )


# ── Main ────────────────────────────────────────────────────────────────────
if data_source == "CubeAnalytics API":
    if not api_installation:
        st.info("👈 Select an installation in the left panel.")
        st.stop()

    if not api_date_from or not api_date_to:
        st.info("Select both start and end dates in the calendar.")
        st.stop()

    if api_date_from > api_date_to:
        st.error("'From' date must be before 'To' date.")
        st.stop()

    with st.spinner(f"Loading data from CubeAnalytics API for {api_installation['name']}..."):
        try:
            df_raw = query_port_wait_time(
                api_installation["id"], str(api_date_from), str(api_date_to),
            )
        except Exception as e:
            st.error(f"API query failed: {e}")
            st.stop()

    if df_raw.empty:
        st.warning("No data returned for the selected installation and date range.")
        st.stop()

    result, error = process_dataframe(df_raw)
    if error:
        st.error(f"Processing error: {error}")
        st.stop()

    install_name = re.sub(r"[^a-zA-Z0-9_-]", "_", api_installation["name"])
    output_name = f"{install_name}_{api_date_from}_{api_date_to}_Pivot_Ready.csv"

    st.markdown(f"**{api_installation['name']}** — {api_date_from} to {api_date_to}")
    st.divider()
    show_result(result, output_name, "api_0")

else:
    if not uploaded_files:
        st.info("👈 Upload CSV file(s) in the left panel to get started.")
        st.markdown(
            """
            ---
            ### How it works

            1. Upload one or more CSV files from Unify Analytics / CubeAnalytics
            2. Each file is processed **independently** (files are never merged)
            3. Download the output pivot-ready CSV for import into Google Sheets

            ### Processing includes
            - Extract Date + Hour from Timestamp
            - Group by: Date × Hour × Pick type × Port ID × Category
            - **Weighted averages** for bin wait time and operator handling time
            - Estimate **Average open ports** = (bin_wait + op_handling) × Count / 3600 (capped at 1)
            - Rows with Count = 0 are excluded
            """
        )
        st.stop()

    st.markdown(f"**Files uploaded: {len(uploaded_files)}**")
    st.divider()

    for i, uploaded_file in enumerate(uploaded_files):
        raw = uploaded_file.getvalue()
        output_name = make_output_name(uploaded_file.name)

        with st.expander(f"📄 {uploaded_file.name} → {output_name}", expanded=(i == 0)):
            result, error = process_file(raw, uploaded_file.name)

            if error:
                st.error(f"Error: {error}")
                continue

            show_result(result, output_name, f"csv_{i}")
