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
        "Upload picking export CSV",
        type=["csv"],
        help="Semicolon-delimited (;) CSV with order-level / pick-task-level export",
    )
    plan_file = st.file_uploader(
        "Upload plan file (optional)",
        type=["csv"],
        help="Semicolon-delimited (;) plan file with order-planned-{H} columns",
    )
    st.divider()
    time_shift = st.slider(
        "Plan → picking time shift (hours)",
        min_value=0, max_value=6, value=3,
        help="Hours before prioritization time that picking starts. "
             "Shifts the plan/actual overlay left to align with picking times.",
    )
    show_comparison = st.checkbox("Show AS91 vs AS92 comparison", value=True)
    show_hourly = st.checkbox("Show hourly distribution", value=True)
    st.divider()
    st.markdown(
        "**Required columns (picking):**\n"
        "- `AutoStore`\n"
        "- `Type`\n"
        "- `Prioritization Time`\n"
        "- `Finished Picking At`\n\n"
        "**Plan file columns:**\n"
        "- `Date`\n"
        "- `order-planned-{H}`\n\n"
        "💡 *Upload 2 days of picking data to see pre-picked share per hour.*"
    )


# ── Chart function ──────────────────────────────────────────────────────────
def generate_chart(data, autostore_num, warehouse_name, plan_pct=None, time_shift_h=3, full_data=None):
    fig, ax = plt.subplots(figsize=(24, 10), dpi=150)
    ax.set_facecolor("#f8f8f8")
    fig.patch.set_facecolor("white")
    ax.grid(True, alpha=0.3, color="#cccccc")

    next_day = data[data["is_next_day"]]
    same_day = data[~data["is_next_day"]]
    late = same_day[same_day["diff_minutes"] < 0]
    std_on = same_day[(same_day["diff_minutes"] >= 0) & (same_day["Type"] == "STANDARD")]
    exp_on = same_day[(same_day["diff_minutes"] >= 0) & (same_day["Type"] == "EXPRESS")]
    total = len(data)
    late_pct = (len(late) / total * 100) if total > 0 else 0

    ax.scatter(
        next_day["Finished Picking At"], next_day["diff_minutes"],
        c="#999999", s=6, alpha=0.35,
        label=f"Next-day pre-pick ({len(next_day):,})",
    )
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

    if plan_pct is not None and not plan_pct.empty:
        ax2 = ax.twinx()
        base_date = data["Finished Picking At"].dt.normalize().iloc[0]
        shifted_hours = plan_pct["hour"].values - time_shift_h
        x_times = [base_date + pd.Timedelta(hours=float(h)) for h in shifted_hours]

        # Target date = the date shown on the chart (Finished Picking At date)
        chart_target_date = base_date.date()

        # Use full_data (all days) to capture pre-picks from day before
        overlay_src = full_data if full_data is not None else data

        # All orders with Prioritization Time on target date (includes pre-picks from day before)
        all_for_target = overlay_src[overlay_src["Prioritization Time"].dt.date == chart_target_date]
        all_prio_counts = all_for_target.groupby("prio_hour").size()
        all_total = all_prio_counts.sum()

        # Same-day only: prio date = target AND finished date = target
        sameday_for_target = all_for_target[
            all_for_target["Finished Picking At"].dt.date == chart_target_date
        ]
        sameday_prio_counts = sameday_for_target.groupby("prio_hour").size()
        sameday_total = all_total  # normalize both to same total for comparison

        actual_pct_vals = []
        sameday_pct_vals = []
        for h in plan_pct["hour"].values:
            actual_pct_vals.append(
                (all_prio_counts.get(h, 0) / all_total * 100) if all_total > 0 else 0
            )
            sameday_pct_vals.append(
                (sameday_prio_counts.get(h, 0) / sameday_total * 100) if sameday_total > 0 else 0
            )

        plan_arr = plan_pct["plan_pct"].values
        actual_arr = np.array(actual_pct_vals)
        sameday_arr = np.array(sameday_pct_vals)

        ax2.plot(
            x_times, plan_arr,
            color="#9467bd", linewidth=2.5, linestyle="--", alpha=0.8,
            label=f"Plan shape % (shifted -{time_shift_h}h)",
        )
        ax2.plot(
            x_times, actual_arr,
            color="#d62728", linewidth=2.5, linestyle="-", alpha=0.8,
            label=f"Actual shape % (shifted -{time_shift_h}h)",
        )
        ax2.plot(
            x_times, sameday_arr,
            color="#d62728", linewidth=1.8, linestyle=":", alpha=0.6,
            label=f"Same-day only % (shifted -{time_shift_h}h)",
        )
        ax2.fill_between(
            x_times, sameday_arr, actual_arr,
            alpha=0.10, color="#999999", label="Pre-picked (day before)",
        )
        ax2.fill_between(
            x_times, plan_arr, actual_arr,
            where=plan_arr > actual_arr,
            alpha=0.06, color="#9467bd", label="Surplus (plan > actual)",
        )
        ax2.fill_between(
            x_times, plan_arr, actual_arr,
            where=actual_arr > plan_arr,
            alpha=0.06, color="#d62728", label="Deficit (actual > plan)",
        )
        ax2.set_ylabel("% of daily orders (shape)", fontsize=13)
        ax2.legend(loc="upper right", fontsize=11, framealpha=0.9)

    plt.tight_layout()
    return fig


def compute_stats(data):
    total = len(data)
    if total == 0:
        return {}
    same_day = data[~data["is_next_day"]]
    next_day = data[data["is_next_day"]]
    on_time = len(same_day[same_day["diff_minutes"] >= 0])
    late = len(same_day[same_day["diff_minutes"] < 0])
    return {
        "Total": total,
        "Same-day": len(same_day),
        "Next-day": len(next_day),
        "On Time": on_time,
        "On Time %": round(on_time / len(same_day) * 100, 1) if len(same_day) > 0 else 0,
        "Late": late,
        "Late %": round(late / len(same_day) * 100, 1) if len(same_day) > 0 else 0,
        "Median same-day (min)": round(same_day["diff_minutes"].median(), 1) if len(same_day) > 0 else 0,
        "Mean same-day (min)": round(same_day["diff_minutes"].mean(), 1) if len(same_day) > 0 else 0,
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
df["is_next_day"] = df["Prioritization Time"].dt.date > df["Finished Picking At"].dt.date
df["prio_hour"] = df["Prioritization Time"].dt.hour

df_91 = df[df["AutoStore"].str.contains(".91", regex=False)].copy()
df_92 = df[df["AutoStore"].str.contains(".92", regex=False)].copy()
parts = df["AutoStore"].iloc[0].split(".")
warehouse = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else "unknown"

dates = df["Finished Picking At"].dt.date
date_range = (
    f"{dates.min()} – {dates.max()}" if dates.min() != dates.max() else str(dates.min())
)

# ── Parse plan file (optional) — convert to % shape ─────────────────────────
plan_pct = None
target_date = None
if plan_file is not None:
    try:
        plan_raw = pd.read_csv(plan_file, sep=";")
        plan_raw["parsed_date"] = pd.to_datetime(
            plan_raw["Date"].str.extract(r"(\w+ \w+ \d+ \d+)")[0], format="%a %b %d %Y"
        )
        available_dates = sorted(df["Finished Picking At"].dt.date.unique())
        pick_date = available_dates[-1]  # latest date = target day
        plan_day = plan_raw[plan_raw["parsed_date"].dt.date == pick_date]
        if not plan_day.empty:
            target_date = pick_date
            row = plan_day.iloc[0]
            plan_rows = []
            for h in range(3, 23):
                p_col = f"order-planned-{h}"
                p_val = 0
                if p_col in row.index and pd.notna(row[p_col]) and str(row[p_col]).strip():
                    p_val = float(str(row[p_col]).replace(",", ""))
                plan_rows.append({"hour": h, "planned": p_val})
            plan_df = pd.DataFrame(plan_rows)
            total_plan = plan_df["planned"].sum()
            if total_plan > 0:
                plan_df["plan_pct"] = plan_df["planned"] / total_plan * 100
            else:
                plan_df["plan_pct"] = 0.0
            plan_pct = plan_df[["hour", "plan_pct"]]
            st.success(f"Plan file loaded for **{pick_date}** — shape overlay enabled (shift: -{time_shift}h)")
        else:
            st.warning(f"Plan file has no data for {pick_date}")
    except Exception as e:
        st.warning(f"Could not parse plan file: {e}")

# When plan is loaded, filter scatter to target day only
if target_date is not None:
    df_91_scatter = df_91[df_91["Finished Picking At"].dt.date == target_date].copy()
    df_92_scatter = df_92[df_92["Finished Picking At"].dt.date == target_date].copy()
else:
    df_91_scatter = df_91
    df_92_scatter = df_92

# ── Info bar ────────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Warehouse", warehouse)
col2.metric("Date", str(target_date) if target_date else date_range)
col3.metric("AutoStore 91", f"{len(df_91_scatter):,}")
col4.metric("AutoStore 92", f"{len(df_92_scatter):,}")

st.divider()

# ── AutoStore 91 ────────────────────────────────────────────────────────────
st.header("AutoStore 91")
stats_91 = compute_stats(df_91_scatter)

if stats_91:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total", f"{stats_91['Total']:,}")
    c2.metric("Same-day", f"{stats_91['Same-day']:,}")
    c3.metric("Next-day", f"{stats_91['Next-day']:,}")
    c4.metric("On Time", f"{stats_91['On Time %']}%")
    c5.metric("Late", f"{stats_91['Late %']}%")
    c6.metric("Median", f"{stats_91['Median same-day (min)']} min")

    fig_91 = generate_chart(df_91_scatter, 91, warehouse, plan_pct=plan_pct, time_shift_h=time_shift, full_data=df_91)
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
stats_92 = compute_stats(df_92_scatter)

if stats_92:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total", f"{stats_92['Total']:,}")
    c2.metric("Same-day", f"{stats_92['Same-day']:,}")
    c3.metric("Next-day", f"{stats_92['Next-day']:,}")
    c4.metric("On Time", f"{stats_92['On Time %']}%")
    c5.metric("Late", f"{stats_92['Late %']}%")
    c6.metric("Median", f"{stats_92['Median same-day (min)']} min")

    fig_92 = generate_chart(df_92_scatter, 92, warehouse, plan_pct=plan_pct, time_shift_h=time_shift, full_data=df_92)
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
