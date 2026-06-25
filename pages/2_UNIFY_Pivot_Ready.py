import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(
    page_title="UNIFY Pivot Ready",
    page_icon="🔄",
    layout="wide",
)

st.title("🔄 UNIFY Pivot Ready")
st.markdown(
    "Convert Unify Analytics / CubeAnalytics CSV exports into **pivot-ready long format** CSV."
)


def make_output_name(input_name):
    """Derive output filename: 'CubeAnalytics (9).csv' → 'CubeAnalytics_9_Pivot_Ready.csv'"""
    base = input_name.rsplit(".", 1)[0]
    clean = re.sub(r"\s*\((\d+)\)", r"_\1", base)
    clean = clean.replace(" ", "_")
    return f"{clean}_Pivot_Ready.csv"


def process_file(raw_bytes, filename):
    """Process one CSV file per the UNIFY_PIVOT_READY_V13 manual."""
    # Step 1 — Load & parse
    df = pd.read_csv(io.BytesIO(raw_bytes))

    required = ["Timestamp", "Port ID", "Pick type", "Count",
                 "Average bin wait time", "Average operator handling time"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return None, f"Missing columns: {missing}"

    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.dropna(subset=["Timestamp"])

    # Step 2 — Extract date components
    df["Date"] = df["Timestamp"].dt.date
    df["Hour"] = df["Timestamp"].dt.hour

    # Step 3 — Clean Category
    if "Category" not in df.columns:
        df["Category"] = ""
    df["Category"] = df["Category"].fillna("")
    df["Category"] = df["Category"].apply(
        lambda x: str(int(float(x))) if x != "" and str(x).replace(".", "", 1).isdigit() else str(x) if x != "" else ""
    )

    # Ensure numeric columns
    for col in ["Count", "Average bin wait time", "Average operator handling time"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Weighted components for aggregation
    df["_w_bin_wait"] = df["Count"] * df["Average bin wait time"]
    df["_w_op_handling"] = df["Count"] * df["Average operator handling time"]

    # Step 4 — Group & aggregate
    group_cols = ["Date", "Hour", "Pick type", "Port ID", "Category"]
    agg = df.groupby(group_cols, dropna=False).agg(
        Count=("Count", "sum"),
        _w_bin_wait=("_w_bin_wait", "sum"),
        _w_op_handling=("_w_op_handling", "sum"),
    ).reset_index()

    # Exclude rows with Count = 0
    agg = agg[agg["Count"] > 0].copy()

    # Weighted averages
    agg["Average bin wait time"] = agg["_w_bin_wait"] / agg["Count"]
    agg["Average operator handling time"] = agg["_w_op_handling"] / agg["Count"]

    # Average open ports estimate (capped at 1 — one port cannot be more than fully open)
    agg["Average open ports"] = (
        (agg["Average bin wait time"] + agg["Average operator handling time"])
        * agg["Count"] / 3600
    ).clip(upper=1)

    # Step 5 — Sort
    agg = agg.sort_values(["Date", "Hour", "Pick type", "Port ID", "Category"])

    # Final output columns
    result = agg[[
        "Date", "Hour", "Pick type", "Port ID", "Category",
        "Count", "Average bin wait time", "Average operator handling time",
        "Average open ports"
    ]].copy()
    result = result.rename(columns={"Port ID": "Port number"})

    # Round numeric columns
    for col in ["Average bin wait time", "Average operator handling time", "Average open ports"]:
        result[col] = result[col].round(4)
    result["Count"] = result["Count"].astype(int)

    return result, None


# ── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
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


# ── Main ────────────────────────────────────────────────────────────────────
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

# Process each file
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

        # Stats
        col1, col2, col3, col4 = st.columns(4)
        dates = result["Date"].unique()
        col1.metric("Rows", f"{len(result):,}")
        col2.metric("Days", len(dates))
        col3.metric("Ports", result["Port number"].nunique())
        col4.metric("Pick types", result["Pick type"].nunique())

        # Preview
        st.markdown("**Preview (first 20 rows):**")
        st.dataframe(result.head(20), use_container_width=True)

        # Download
        csv_bytes = result.to_csv(index=False).encode("utf-8")
        st.download_button(
            f"⬇️ Download {output_name}",
            data=csv_bytes,
            file_name=output_name,
            mime="text/csv",
            key=f"download_{i}",
        )
