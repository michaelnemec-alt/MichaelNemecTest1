import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io

st.set_page_config(
    page_title="Prio vs Picking Chart",
    page_icon="📊",
    layout="wide",
)

# ── Header ──────────────────────────────────────────────────────────────────
st.title("📊 Prio Time vs Picking Finished At")
st.markdown(
    "Scatter plot showing whether AutoStore orders were picked "
    "**on time** or **late** relative to the prioritization time."
)

# ── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    uploaded_file = st.file_uploader(
        "Upload CSV file",
        type=["csv"],
        help="Semicolon-delimited (;) CSV with order-level / pick-task-level export",
    )
    st.divider()
    show_comparison = st.checkbox("Show AS91 vs AS92 comparison", value=True)
    show_hourly = st.checkbox("Show hourly distribution", value=True)
    st.divider()
    st.markdown(
        "**Required columns:**\n"
        "- `AutoStore`\n"
        "- `Type`\n"
        "- `Prioritization Time`\n"
        "- `Finished Picking At`"
    )


# ── Chart function ──────────────────────────────────────────────────────────
def generate_chart(data, autostore_num, warehouse_name):
    fig, ax = plt.subplots(figsize=(24, 10), dpi=150)
    ax.set_facecolor("#f8f8f8")
    fig.patch.set_facecolor("white")
    ax.grid(True, alpha=0.3, color="#cccccc")

    late = data[data["diff_minutes"] < 0]
    std_on = data[(data["diff_minutes"] >= 0) & (data["Type"] == "STANDARD")]
    exp_on = data[(data["diff_minutes"] >= 0) & (data["Type"] == "EXPRESS")]
    total = len(data)
    late_pct = (len(late) / total * 100) if total > 0 else 0

    ax.scatter(
        std_on["Finished Picking At"], std_on["diff_minutes"],
        c="#1f77b4", s=6, alpha=0.45,
        label=f"STANDARD on time ({len(std_on):,})",
    )
    ax.scatter(
        exp_on["Finished Picking At"], exp_on["diff_minutes"],
        c="#2ca02c", s=6, alpha=0.45,
        label=f"EXPRESS on time ({len(exp_on):,})",
    )
    ax.scatter(
        late["Finished Picking At"], late["diff_minutes"],
        c="#ff7f0e", s=6, alpha=0.45,
        label=f"Late ({len(late):,} orders, {late_pct:.1f}%)",
    )

    ax.axhline(y=0, color="black", linewidth=2)
    ylim = ax.get_ylim()
    ax.axhspan(0, ylim[1] * 1.5, alpha=0.03, color="green")
    ax.axhspan(ylim[0] * 1.5, 0, alpha=0.03, color="red")
    ax.set_ylim(ylim)

    ax.legend(loc="upper left", fontsize=12, framealpha=0.9)
    xlim = ax.get_xlim()
    x_pos = xlim[0] + (xlim[1] - xlim[0]) * 0.02
    ax.text(x_pos, ylim[1] * 0.45, "+ ON TIME", fontsize=16,
            fontweight="bold", color="#1f77b4", alpha=0.7, va="center")
    ax.text(x_pos, min(ylim[0] * 0.7, -20), "- LATE", fontsize=16,
            fontweight="bold", color="#ff7f0e", alpha=0.7, va="center")

    ax.set_title(
        f"Prio Time vs Picking Finished At — AutoStore {autostore_num}\n"
        f"(Types: STANDARD + EXPRESS) | {warehouse_name}",
        fontsize=16, fontweight="bold",
    )
    ax.set_xlabel("Finished Picking At", fontsize=13)
    ax.set_ylabel("Minutes (+ on time / - late)", fontsize=13)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    ax.yaxis.set_major_locator(plt.MultipleLocator(100))
    ax.yaxis.set_minor_locator(plt.MultipleLocator(50))
    ax.grid(which="minor", axis="y", alpha=0.2, color="#cccccc", linestyle="--")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    return fig


def compute_stats(data):
    total = len(data)
    if total == 0:
        return {}
    on_time = len(data[data["diff_minutes"] >= 0])
    late = total - on_time
    return {
        "Total": total,
        "On Time": on_time,
        "On Time %": round(on_time / total * 100, 1),
        "Late": late,
        "Late %": round(late / total * 100, 1),
        "Median (min)": round(data["diff_minutes"].median(), 1),
        "Mean (min)": round(data["diff_minutes"].mean(), 1),
        "Max early (min)": round(data["diff_minutes"].max(), 1),
        "Max late (min)": round(data["diff_minutes"].min(), 1),
    }


def fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


# ── Main ────────────────────────────────────────────────────────────────────
if uploaded_file is None:
    st.info("👈 Upload a CSV file in the left panel to get started.")
    st.stop()

# Load data
try:
    df_raw = pd.read_csv(uploaded_file, sep=";")
except Exception as e:
    st.error(f"Error reading CSV: {e}\n\nMake sure the file is semicolon-delimited (;)")
    st.stop()

# Validate columns
required = ["AutoStore", "Type", "Prioritization Time", "Finished Picking At"]
missing = [c for c in required if c not in df_raw.columns]
if missing:
    st.error(
        f"Missing columns: **{missing}**\n\n"
        "Please use an order-level / pick-task-level export (not detail-level)."
    )
    st.stop()

# Process
df = df_raw[df_raw["Type"].isin(["STANDARD", "EXPRESS"])].copy()
df["Prioritization Time"] = pd.to_datetime(df["Prioritization Time"], errors="coerce")
df["Finished Picking At"] = pd.to_datetime(df["Finished Picking At"], errors="coerce")
df = df.dropna(subset=["Prioritization Time", "Finished Picking At"])
df["diff_minutes"] = (
    (df["Prioritization Time"] - df["Finished Picking At"]).dt.total_seconds() / 60
)

df_91 = df[df["AutoStore"].str.contains(".91", regex=False)].copy()
df_92 = df[df["AutoStore"].str.contains(".92", regex=False)].copy()
parts = df["AutoStore"].iloc[0].split(".")
warehouse = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else "unknown"

dates = df["Finished Picking At"].dt.date
date_range = (
    f"{dates.min()} – {dates.max()}" if dates.min() != dates.max() else str(dates.min())
)

# ── Info bar ────────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Warehouse", warehouse)
col2.metric("Date", date_range)
col3.metric("AutoStore 91", f"{len(df_91):,}")
col4.metric("AutoStore 92", f"{len(df_92):,}")

st.divider()

# ── AutoStore 91 ────────────────────────────────────────────────────────────
st.header("AutoStore 91")
stats_91 = compute_stats(df_91)

if stats_91:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", f"{stats_91['Total']:,}")
    pct_91 = stats_91["On Time %"]
    c2.metric("On Time", f"{pct_91}%", delta=None)
    c3.metric("Late", f"{stats_91['Late %']}%")
    c4.metric("Median", f"{stats_91['Median (min)']} min")
    c5.metric("Mean", f"{stats_91['Mean (min)']} min")

    fig_91 = generate_chart(df_91, 91, warehouse)
    st.pyplot(fig_91)

    st.download_button(
        "⬇️ Download PNG — AutoStore 91",
        data=fig_to_bytes(fig_91),
        file_name=f"prio_vs_picking_{warehouse}_autostore_91.png",
        mime="image/png",
    )
    plt.close(fig_91)
else:
    st.warning("No data for AutoStore 91")

st.divider()

# ── AutoStore 92 ────────────────────────────────────────────────────────────
st.header("AutoStore 92")
stats_92 = compute_stats(df_92)

if stats_92:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", f"{stats_92['Total']:,}")
    c2.metric("On Time", f"{stats_92['On Time %']}%")
    c3.metric("Late", f"{stats_92['Late %']}%")
    c4.metric("Median", f"{stats_92['Median (min)']} min")
    c5.metric("Mean", f"{stats_92['Mean (min)']} min")

    fig_92 = generate_chart(df_92, 92, warehouse)
    st.pyplot(fig_92)

    st.download_button(
        "⬇️ Download PNG — AutoStore 92",
        data=fig_to_bytes(fig_92),
        file_name=f"prio_vs_picking_{warehouse}_autostore_92.png",
        mime="image/png",
    )
    plt.close(fig_92)
else:
    st.warning("No data for AutoStore 92")

# ── Comparison table ────────────────────────────────────────────────────────
if show_comparison and stats_91 and stats_92:
    st.divider()
    st.header("AS91 vs AS92 Comparison")
    comp = pd.DataFrame(
        {"AutoStore 91": stats_91, "AutoStore 92": stats_92}
    ).T
    st.dataframe(comp, use_container_width=True)

# ── Hourly distribution ────────────────────────────────────────────────────
if show_hourly:
    st.divider()
    st.header("Hourly Pick Task Distribution")

    df["hour"] = df["Finished Picking At"].dt.hour
    hourly = (
        df.groupby(["hour", df["AutoStore"].str.extract(r"\.(\d{2})", expand=False)])
        .size()
        .unstack(fill_value=0)
    )
    hourly.columns = [f"AS{c}" for c in hourly.columns]

    fig_h, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 6))

    hourly.plot(kind="bar", ax=ax1, color=["#1f77b4", "#ff7f0e"])
    ax1.set_title(f"Pick Tasks per Hour — {warehouse}", fontsize=14, fontweight="bold")
    ax1.set_xlabel("Hour")
    ax1.set_ylabel("Count")
    ax1.grid(axis="y", alpha=0.3)

    hourly_pct = hourly.div(hourly.sum(axis=1), axis=0) * 100
    hourly_pct.plot(kind="bar", stacked=True, ax=ax2, color=["#1f77b4", "#ff7f0e"])
    ax2.set_title(f"AutoStore Share per Hour — {warehouse}", fontsize=14, fontweight="bold")
    ax2.set_xlabel("Hour")
    ax2.set_ylabel("Share (%)")
    ax2.set_ylim(0, 100)
    ax2.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    st.pyplot(fig_h)

    st.download_button(
        "⬇️ Download PNG — Hourly Distribution",
        data=fig_to_bytes(fig_h),
        file_name=f"hourly_distribution_{warehouse}.png",
        mime="image/png",
    )
    plt.close(fig_h)
