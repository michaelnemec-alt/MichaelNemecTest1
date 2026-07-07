import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io
from datetime import date, timedelta

from snowflake_utils import is_snowflake_configured, get_available_warehouses, query_picking_data


def _parse_plan(plan_file_data, target_date, actual_by_prio_hour):
    plan_raw = pd.read_csv(io.BytesIO(plan_file_data), sep=";")
    plan_raw["parsed_date"] = pd.to_datetime(
        plan_raw["Date"].str.extract(r"(\w+ \w+ \d+ \d+)")[0], format="%a %b %d %Y"
    )
    plan_day = plan_raw[plan_raw["parsed_date"].dt.date == target_date]
    if plan_day.empty:
        return None
    row = plan_day.iloc[0]
    plan_rows = []
    for h in range(5, 23):
        p_col = f"order-planned-{h}"
        p_val = 0
        if p_col in row.index and pd.notna(row[p_col]) and str(row[p_col]).strip():
            p_val = float(str(row[p_col]).replace(",", ""))
        a_row = actual_by_prio_hour[actual_by_prio_hour["prio_hour"] == h]
        a_val = int(a_row["actual"].iloc[0]) if not a_row.empty else 0
        plan_rows.append({"hour": h, "planned": p_val, "actual": a_val})
    return pd.DataFrame(plan_rows)


def _fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


def render():
    sf_available = is_snowflake_configured()

    with st.sidebar:
        st.markdown("#### Day Evaluation")

        if sf_available:
            data_source = st.radio("Picking data source", ["CSV Upload", "Snowflake"], index=0, key="day_ds")
        else:
            data_source = "CSV Upload"

        picking_file = None
        sf_warehouse = sf_date_from = sf_date_to = None

        if data_source == "Snowflake":
            warehouses = get_available_warehouses()
            sf_warehouse = st.selectbox("Warehouse", warehouses, key="day_wh")
            col_f, col_t = st.columns(2)
            with col_f:
                sf_date_from = st.date_input("From", value=date.today() - timedelta(days=2), key="day_from")
            with col_t:
                sf_date_to = st.date_input("To", value=date.today() - timedelta(days=1), key="day_to")
        else:
            picking_file = st.file_uploader("Picking export CSV *", type=["csv"],
                                             help="Semicolon-delimited (;) CSV", key="day_csv")

        plan_file = st.file_uploader("Plan file CSV *", type=["csv"],
                                      help="Semicolon-delimited (;) plan file", key="day_plan")
        st.divider()
        st.markdown("**Both picking data + plan file required.**")

    st.markdown(
        "Evaluate current day progress: plan vs actual orders, "
        "pre-pick buffer status, and capacity recommendations."
    )

    df_raw = None
    if data_source == "Snowflake":
        if sf_warehouse and sf_date_from and sf_date_to:
            with st.spinner("Loading from Snowflake..."):
                try:
                    df_raw = query_picking_data(sf_warehouse, str(sf_date_from), str(sf_date_to))
                except Exception as e:
                    st.error(f"Snowflake query failed: {e}")
                    return
            if df_raw.empty:
                st.warning("No data found.")
                return
        else:
            st.info("Select warehouse and date range in the sidebar.")
            return
    else:
        if picking_file is None:
            st.info("Upload the picking CSV in the sidebar.")
            return
        try:
            df_raw = pd.read_csv(picking_file, sep=";")
        except Exception as e:
            st.error(f"Error reading picking CSV: {e}")
            return

    if plan_file is None:
        st.info("Upload the **plan file** in the sidebar to continue.")
        st.markdown(
            """
            ---
            ### How it works

            1. Provide **picking data** (CSV upload or Snowflake)
            2. Upload the **plan file** (contains planned orders per hour)
            3. The tool analyzes:
               - **Scissors indicator** — cumulative gap between plan and actual demand
               - **Pre-pick buffer** — how far ahead orders are being picked
               - **Next-day pre-picking volume** — capacity being used for tomorrow
               - **Decision support** — whether to play safe or release capacity
            """
        )
        return

    required = ["AutoStore", "Type", "Prioritization Time", "Finished Picking At"]
    missing = [c for c in required if c not in df_raw.columns]
    if missing:
        st.error(f"Missing columns in picking data: **{missing}**")
        return

    df = df_raw[df_raw["Type"].isin(["STANDARD", "EXPRESS"])].copy()
    df["Prioritization Time"] = pd.to_datetime(df["Prioritization Time"], errors="coerce")
    df["Finished Picking At"] = pd.to_datetime(df["Finished Picking At"], errors="coerce")
    df = df.dropna(subset=["Prioritization Time", "Finished Picking At"])
    df["diff_minutes"] = (df["Prioritization Time"] - df["Finished Picking At"]).dt.total_seconds() / 60
    df["is_next_day"] = df["Prioritization Time"].dt.date > df["Finished Picking At"].dt.date
    df["hour"] = df["Finished Picking At"].dt.hour
    df["prio_hour"] = df["Prioritization Time"].dt.hour

    pick_date = df["Finished Picking At"].dt.date.min()
    actual_by_prio_hour = df.groupby("prio_hour").size().reset_index(name="actual")

    plan_hourly = _parse_plan(plan_file.getvalue(), pick_date, actual_by_prio_hour)
    if plan_hourly is None:
        st.error(f"Plan file has no data matching the picking date: **{pick_date}**")
        return

    plan_hourly["cum_planned"] = plan_hourly["planned"].cumsum()
    plan_hourly["cum_actual"] = plan_hourly["actual"].cumsum()
    plan_hourly["cum_surplus"] = plan_hourly["cum_planned"] - plan_hourly["cum_actual"]
    plan_hourly["hourly_gap"] = plan_hourly["planned"] - plan_hourly["actual"]

    total_planned = plan_hourly["planned"].sum()
    total_actual = plan_hourly["actual"].sum()
    final_surplus = plan_hourly["cum_surplus"].iloc[-1]

    same_day = df[~df["is_next_day"]]
    next_day = df[df["is_next_day"]]
    late = same_day[same_day["diff_minutes"] < 0]

    hourly_pick = same_day.groupby("hour").agg(
        pick_count=("diff_minutes", "size"),
        median_prepick=("diff_minutes", "median"),
        p10_prepick=("diff_minutes", lambda x: x.quantile(0.1)),
    ).reset_index()

    st.markdown(f"### Analysis for **{pick_date}**")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Picks", f"{len(df):,}")
    c2.metric("Same-day", f"{len(same_day):,}")
    c3.metric("Next-day", f"{len(next_day):,}", delta=f"{len(next_day)/len(df)*100:.1f}%")
    c4.metric("Late", f"{len(late):,}")
    scissors_val = final_surplus
    scissors_delta = "surplus" if scissors_val >= 0 else "deficit"
    c5.metric("Plan Gap (EOD)", f"{scissors_val:+.0f}", delta=scissors_delta,
              delta_color="normal" if scissors_val >= 0 else "inverse")
    st.divider()

    st.markdown("#### Scissors — Plan vs Actual (Cumulative)")
    hours = plan_hourly["hour"].values

    fig_sc, (ax1, ax2) = plt.subplots(2, 1, figsize=(18, 12), sharex=True)
    ax1.bar(hours - 0.2, plan_hourly["planned"], width=0.4, color="#9467bd", alpha=0.7, label="Planned")
    ax1.bar(hours + 0.2, plan_hourly["actual"], width=0.4, color="#d62728", alpha=0.7, label="Actual")
    ax1.set_ylabel("Orders per hour")
    ax1.set_title(f"Hourly Orders — Plan vs Actual | {pick_date}", fontsize=14, fontweight="bold")
    ax1.legend(fontsize=12)
    ax1.grid(axis="y", alpha=0.3)

    ax2.plot(hours, plan_hourly["cum_surplus"], color="#2c3e50", linewidth=2.5, marker="o", markersize=5)
    ax2.fill_between(hours, 0, plan_hourly["cum_surplus"],
                     where=plan_hourly["cum_surplus"] >= 0,
                     alpha=0.15, color="#9467bd", label="Surplus (buffer building)")
    ax2.fill_between(hours, 0, plan_hourly["cum_surplus"],
                     where=plan_hourly["cum_surplus"] < 0,
                     alpha=0.15, color="#d62728", label="Deficit (backlog risk)")
    ax2.axhline(y=0, color="black", linewidth=1, linestyle="--")
    ax2.set_xlabel("Hour")
    ax2.set_ylabel("Cumulative gap (plan - actual)")
    ax2.set_title("Cumulative Plan Surplus / Deficit (Scissors)", fontsize=14, fontweight="bold")
    ax2.legend(fontsize=12)
    ax2.grid(alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig_sc)
    st.download_button("Download Scissors Chart", data=_fig_to_bytes(fig_sc),
                       file_name=f"scissors_{pick_date}.png", mime="image/png", key="dl_scissors")
    plt.close(fig_sc)
    st.divider()

    st.markdown("#### Buffer Assessment")
    fig_buf, ax = plt.subplots(figsize=(18, 7))
    if not hourly_pick.empty:
        ax.bar(hourly_pick["hour"], hourly_pick["pick_count"], color="#1f77b4", alpha=0.6, label="Same-day picks")
        ax2 = ax.twinx()
        ax2.plot(hourly_pick["hour"], hourly_pick["median_prepick"], color="#2ca02c",
                 linewidth=2.5, marker="o", markersize=6, label="Median buffer (min)")
        ax2.plot(hourly_pick["hour"], hourly_pick["p10_prepick"], color="#ff7f0e",
                 linewidth=2, marker="s", markersize=5, linestyle="--", label="P10 buffer (min)")
        ax2.axhline(y=0, color="red", linewidth=1.5, linestyle="--", alpha=0.7)
        ax2.set_ylabel("Pre-pick buffer (minutes)", fontsize=12)
        ax2.legend(loc="upper right", fontsize=11)
    ax.set_xlabel("Hour", fontsize=12)
    ax.set_ylabel("Pick count (same-day)", fontsize=12)
    ax.set_title(f"Pre-Pick Buffer Throughout the Day | {pick_date}", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", fontsize=11)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig_buf)
    st.download_button("Download Buffer Chart", data=_fig_to_bytes(fig_buf),
                       file_name=f"buffer_{pick_date}.png", mime="image/png", key="dl_buffer")
    plt.close(fig_buf)
    st.divider()

    st.markdown("#### Next-Day Pre-Picking")
    nd_hourly = next_day.groupby("hour").size().reset_index(name="count")
    sd_hourly = same_day.groupby("hour").size().reset_index(name="count")
    fig_nd, ax = plt.subplots(figsize=(18, 6))
    all_hours = range(0, 24)
    sd_vals = [sd_hourly[sd_hourly["hour"] == h]["count"].sum() for h in all_hours]
    nd_vals = [nd_hourly[nd_hourly["hour"] == h]["count"].sum() for h in all_hours]
    ax.bar(all_hours, sd_vals, color="#1f77b4", alpha=0.7, label="Same-day")
    ax.bar(all_hours, nd_vals, bottom=sd_vals, color="#999999", alpha=0.7, label="Next-day")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Pick Tasks")
    ax.set_title(f"Same-Day vs Next-Day Picks by Hour | {pick_date}", fontsize=14, fontweight="bold")
    ax.legend(fontsize=12)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig_nd)
    plt.close(fig_nd)
    st.divider()

    st.markdown("#### Decision Summary")
    max_deficit = plan_hourly["cum_surplus"].min()
    max_surplus = plan_hourly["cum_surplus"].max()
    nd_pct = len(next_day) / len(df) * 100 if len(df) > 0 else 0
    median_buffer_core = same_day[(same_day["hour"] >= 6) & (same_day["hour"] <= 14)]["diff_minutes"].median()

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Key Indicators**")
        st.markdown(f"""
| Metric | Value |
|--------|-------|
| Total planned orders | {total_planned:,.0f} |
| Total actual orders | {total_actual:,.0f} |
| End-of-day gap | {final_surplus:+,.0f} |
| Max cumulative surplus | {max_surplus:+,.0f} |
| Max cumulative deficit | {max_deficit:+,.0f} |
| Next-day pre-picks | {len(next_day):,} ({nd_pct:.1f}%) |
| Core-hours median buffer | {median_buffer_core:.0f} min |
| Late orders | {len(late):,} |
""")

    with col2:
        st.markdown("**Assessment**")
        if final_surplus > 100:
            st.success(
                "**Plan surplus detected.** The day ended with significantly fewer orders than planned. "
                "The AutoStore likely had spare capacity which was channeled into next-day pre-picks. "
                "Consider whether the morning safety buffer was too conservative."
            )
        elif final_surplus < -100:
            st.error(
                "**Plan deficit detected.** More orders arrived than planned. "
                "The AutoStore was under pressure — check if pre-pick buffers compressed "
                "and whether manual offloading was needed."
            )
        else:
            st.info(
                "**Plan roughly on track.** The actual demand closely matched the plan. "
                "Standard buffer management should have been sufficient."
            )

        if median_buffer_core < 30:
            st.warning(
                f"Core-hours median buffer is only **{median_buffer_core:.0f} min** — "
                "this indicates the AutoStore was running tight. "
                "Consider increasing safety margin or offloading to manual."
            )
        elif median_buffer_core > 120:
            st.info(
                f"Core-hours median buffer is **{median_buffer_core:.0f} min** — "
                "comfortable margin. Capacity could potentially be released."
            )

    st.divider()
    st.markdown("#### Per AutoStore Breakdown")

    for as_label, as_filter in [("AS91 (Chilled)", ".91"), ("AS92 (Ambient)", ".92")]:
        as_data = df[df["AutoStore"].str.contains(as_filter, regex=False)]
        if as_data.empty:
            continue
        as_same = as_data[~as_data["is_next_day"]]
        as_next = as_data[as_data["is_next_day"]]
        as_late = as_same[as_same["diff_minutes"] < 0]
        as_median = as_same["diff_minutes"].median() if len(as_same) > 0 else 0

        st.markdown(f"**{as_label}**")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", f"{len(as_data):,}")
        c2.metric("Same-day", f"{len(as_same):,}")
        c3.metric("Next-day", f"{len(as_next):,}")
        c4.metric("Median buffer", f"{as_median:.0f} min")
