import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import re
from datetime import date, timedelta

from snowflake_utils import is_snowflake_configured, get_available_warehouses, query_picking_data
from cubeanalytics_utils import is_api_configured, get_installations, query_port_wait_time


def _generate_chart(data, autostore_num, warehouse_name, full_data=None,
                    target_date=None, plan_planned=None):
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

    ax.scatter(next_day["Finished Picking At"], next_day["diff_minutes"],
               c="#999999", s=6, alpha=0.35, label=f"Next-day pre-pick ({len(next_day):,})")
    ax.scatter(std_on["Finished Picking At"], std_on["diff_minutes"],
               c="#1f77b4", s=6, alpha=0.45, label=f"STANDARD on time ({len(std_on):,})")
    ax.scatter(exp_on["Finished Picking At"], exp_on["diff_minutes"],
               c="#2ca02c", s=6, alpha=0.45, label=f"EXPRESS on time ({len(exp_on):,})")
    ax.scatter(late["Finished Picking At"], late["diff_minutes"],
               c="#ff7f0e", s=6, alpha=0.45, label=f"Late ({len(late):,} orders, {late_pct:.1f}%)")

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

    if full_data is not None and target_date is not None:
        ax2 = ax.twinx()
        base_date = data["Finished Picking At"].dt.normalize().iloc[0]
        hours = list(range(0, 24))
        x_times = [base_date + pd.Timedelta(hours=h) for h in hours]

        all_target = full_data[full_data["Prioritization Time"].dt.date == target_date]
        prepicked = all_target[all_target["Finished Picking At"].dt.date < target_date]
        prepick_counts = prepicked.groupby("prio_hour").size()
        sameday_target = all_target[all_target["Finished Picking At"].dt.date == target_date]
        sameday_counts = sameday_target.groupby("prio_hour").size()

        prepick_vals = np.array([prepick_counts.get(h, 0) for h in hours], dtype=float)
        sameday_vals = np.array([sameday_counts.get(h, 0) for h in hours], dtype=float)
        total_vals = prepick_vals + sameday_vals

        if plan_planned is not None:
            plan_vals = np.array([plan_planned.get(h, 0) for h in hours], dtype=float)
            ax2.fill_between(x_times, plan_vals, alpha=0.12, color="#9467bd")
            ax2.plot(x_times, plan_vals, color="#9467bd", linewidth=2, linestyle="--", alpha=0.7, label="Plan")

        ax2.stackplot(
            x_times, sameday_vals, prepick_vals,
            colors=["#4a90d9", "#d62728"], alpha=0.18,
            labels=[f"Same-day picked ({int(sameday_vals.sum()):,})",
                    f"Pre-picked ({int(prepick_vals.sum()):,})"],
        )
        ax2.plot(x_times, sameday_vals, color="#4a90d9", linewidth=1.5, alpha=0.7)
        ax2.plot(x_times, total_vals, color="#333333", linewidth=1.5, alpha=0.6)
        ax2.set_ylabel("Order count (by prio hour)", fontsize=13)

        left_min, left_max = ax.get_ylim()
        if left_min < 0:
            ratio = abs(left_min) / left_max
            right_max = ax2.get_ylim()[1]
            ax2.set_ylim(-right_max * ratio, right_max)

        ax2.legend(loc="upper right", fontsize=11, framealpha=0.9)

    plt.tight_layout()
    return fig


def _compute_stats(data):
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


def _fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


# AutoStore number -> environment: 91 = non-ambient (Chilled), 92 = Ambient.
_AS_ENV = {91: "Chilled", 92: "Ambient"}
_CAPACITY_CATEGORIES = ["1", "2"]

# Best-effort default: map a picking-export warehouse code (e.g. "hu.bud2") to a
# CubeAnalytics site (city). The user can always override via the site selector.
_WAREHOUSE_CITY_HINTS = {
    "bud": "Biatorbágy",
    "prg": "Praha",
    "vie": "Vienna",
    "muc": "Garching",
    "gar": "Garching",
    "ber": "Schönefeld",
    "sch": "Schönefeld",
}


def _installation_site_map():
    """Group CubeAnalytics installations by site so AS91/AS92 map to the right one.

    Returns {city: {"Chilled": id, "Ambient": id}}. The environment is parsed from
    the installation name, which comes in two shapes: "... (Chilled)" (Praha,
    Vienna, ...) and "... Chilled" (Garching), so the match is parenthesis-agnostic.
    """
    sites = {}
    for inst in get_installations():
        m = re.search(r"(Chilled|Ambient)", inst["name"])
        if not m:
            continue
        city = inst.get("city") or inst["name"]
        sites.setdefault(city, {})[m.group(1)] = inst["id"]
    return sites


def _default_capacity_site(sites, warehouse):
    """Pick a sensible default site for the capacity overlay from the warehouse code."""
    wh = (warehouse or "").lower()
    for hint, city in _WAREHOUSE_CITY_HINTS.items():
        if hint in wh and city in sites:
            return city
    return next(iter(sites), None)


def _capacity_hourly(df_wait, target_date):
    """Aggregate hourly Bin time / User time / bins-per-hour for pick tasks cat 1+2.

    Uses the same source as UNIFY Pivot Ready (port-bin-wait-time):
      Bin time  = count-weighted mean of "Average bin wait time"
      User time = count-weighted mean of "Average operator handling time"
      Bins/hour = sum of Count (bin presentations for picking)
    Restricted to Pick type 'picks' and Category in {1, 2}.
    """
    hours = list(range(24))
    if df_wait is None or df_wait.empty:
        z = [0.0] * 24
        return hours, z, z, z

    d = df_wait[
        df_wait["Category"].isin(_CAPACITY_CATEGORIES)
        & (df_wait["Pick type"] == "picks")
    ].copy()
    d = d[d["Timestamp"].dt.date == target_date]
    if d.empty:
        z = [0.0] * 24
        return hours, z, z, z

    d["hour"] = d["Timestamp"].dt.hour
    d["_wb"] = d["Count"] * d["Average bin wait time"]
    d["_wu"] = d["Count"] * d["Average operator handling time"]
    g = d.groupby("hour").agg(
        count=("Count", "sum"), wb=("_wb", "sum"), wu=("_wu", "sum")
    )

    bin_time, user_time, bins = [], [], []
    for h in hours:
        c = g["count"].get(h, 0)
        bins.append(float(c))
        bin_time.append(float(g["wb"].get(h, 0) / c) if c else 0.0)
        user_time.append(float(g["wu"].get(h, 0) / c) if c else 0.0)
    return hours, bin_time, user_time, bins


def _capacity_chart(df_wait, autostore_num, warehouse_name, target_date, site_name):
    """Combo chart mirroring 'AS Max capacity utilization':

    blue columns = avg Bin wait time, yellow columns = avg Operator handling time
    (left axis, seconds), continuous line = bins picked per hour (right axis).
    Pick tasks category 1 + 2 only.
    """
    hours, bin_time, user_time, bins = _capacity_hourly(df_wait, target_date)

    fig, ax = plt.subplots(figsize=(24, 6), dpi=150)
    ax.set_facecolor("#f8f8f8")
    fig.patch.set_facecolor("white")
    ax.grid(True, axis="y", alpha=0.3, color="#cccccc")

    width = 0.42
    x = np.arange(24)
    ax.bar(x - width / 2, bin_time, width, color="#3f76c4",
           label="Average bin wait time")
    ax.bar(x + width / 2, user_time, width, color="#e8c24a",
           label="Average operator handling time")
    ax.set_ylabel("Seconds (avg per bin)", fontsize=13)
    ax.set_xlabel("Hour", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{h:02d}" for h in hours])

    ax2 = ax.twinx()
    ax2.plot(x, bins, color="#111111", linewidth=2.2, marker="o", markersize=4,
             label="Bins picked / hour (cat 1+2)")
    ax2.set_ylabel("Bins picked / hour", fontsize=13)
    ax2.set_ylim(bottom=0)

    ax.set_title(
        f"AS capacity utilization — AutoStore {autostore_num} "
        f"({_AS_ENV.get(autostore_num, '')})\n"
        f"Pick tasks category 1 + 2 | {site_name} | {target_date}",
        fontsize=15, fontweight="bold",
    )
    lines1, labels1 = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left",
              fontsize=11, framealpha=0.9)

    plt.tight_layout()
    return fig


def _render_capacity_section(autostore_num, site_map, selected_site, target_date, warehouse):
    """Fetch port-wait data for the mapped installation and render the combo chart."""
    env = _AS_ENV.get(autostore_num)
    inst_id = site_map.get(selected_site, {}).get(env)
    if not inst_id:
        st.info(f"No CubeAnalytics {env} installation found for site '{selected_site}'.")
        return
    with st.spinner(f"Loading capacity data (AS{autostore_num} / {env})..."):
        try:
            df_wait = query_port_wait_time(
                inst_id, str(target_date), str(target_date + timedelta(days=1))
            )
        except Exception as e:
            st.error(f"Capacity data query failed: {e}")
            return

    fig = _capacity_chart(df_wait, autostore_num, warehouse, target_date, selected_site)
    st.pyplot(fig)
    st.download_button(
        f"Download PNG — AS{autostore_num} capacity",
        data=_fig_to_bytes(fig),
        file_name=f"capacity_{selected_site}_as{autostore_num}_{target_date}.png",
        mime="image/png", key=f"dl_cap_{autostore_num}",
    )
    plt.close(fig)


def render():
    sf_available = is_snowflake_configured()

    with st.sidebar:
        st.markdown("#### Prio vs Picking")
        if sf_available:
            data_source = st.radio("Data source", ["CSV Upload", "Snowflake"], index=0, key="prio_ds")
        else:
            data_source = "CSV Upload"

        uploaded_file = None
        sf_warehouse = sf_date_from = sf_date_to = None

        if data_source == "Snowflake":
            warehouses = get_available_warehouses()
            sf_warehouse = st.selectbox("Warehouse", warehouses, key="prio_wh")
            col_f, col_t = st.columns(2)
            with col_f:
                sf_date_from = st.date_input("From", value=date.today() - timedelta(days=2), key="prio_from")
            with col_t:
                sf_date_to = st.date_input("To", value=date.today() - timedelta(days=1), key="prio_to")
        else:
            uploaded_file = st.file_uploader("Upload picking export CSV", type=["csv"],
                                             help="Semicolon-delimited (;) CSV", key="prio_csv")

        plan_file = st.file_uploader("Upload plan file (optional)", type=["csv"],
                                      help="Semicolon-delimited (;) plan file", key="prio_plan")
        st.divider()
        show_comparison = st.checkbox("Show AS91 vs AS92 comparison", value=True, key="prio_comp")
        show_hourly = st.checkbox("Show hourly distribution", value=True, key="prio_hourly")
        show_capacity = st.checkbox(
            "Show hourly capacity (Bin/User time)",
            value=is_api_configured(),
            key="prio_cap",
            help="Adds a combo chart per AutoStore: avg Bin/User wait time (bars) "
                 "and bins picked/hour (line) for pick tasks category 1+2, "
                 "using the same CubeAnalytics source as UNIFY Pivot Ready.",
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
        if uploaded_file is None:
            st.info("Upload a CSV file in the sidebar to get started.")
            return
        try:
            df_raw = pd.read_csv(uploaded_file, sep=";")
        except Exception as e:
            st.error(f"Error reading CSV: {e}")
            return

    required = ["AutoStore", "Type", "Prioritization Time", "Finished Picking At"]
    missing = [c for c in required if c not in df_raw.columns]
    if missing:
        st.error(f"Missing columns: **{missing}**")
        return

    df = df_raw[df_raw["Type"].isin(["STANDARD", "EXPRESS"])].copy()
    df["Prioritization Time"] = pd.to_datetime(df["Prioritization Time"], errors="coerce")
    df["Finished Picking At"] = pd.to_datetime(df["Finished Picking At"], errors="coerce")
    df = df.dropna(subset=["Prioritization Time", "Finished Picking At"])
    df["diff_minutes"] = (df["Prioritization Time"] - df["Finished Picking At"]).dt.total_seconds() / 60
    df["is_next_day"] = df["Prioritization Time"].dt.date > df["Finished Picking At"].dt.date
    df["prio_hour"] = df["Prioritization Time"].dt.hour

    df_91 = df[df["AutoStore"].str.contains(".91", regex=False)].copy()
    df_92 = df[df["AutoStore"].str.contains(".92", regex=False)].copy()
    parts = df["AutoStore"].iloc[0].split(".")
    warehouse = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else "unknown"

    available_dates = sorted(df["Finished Picking At"].dt.date.unique())
    if len(available_dates) > 1:
        target_date = st.selectbox("Select target date", options=available_dates,
                                    index=len(available_dates) - 1, key="prio_target_date")
    else:
        target_date = available_dates[0]

    df_91_scatter = df_91[df_91["Finished Picking At"].dt.date == target_date].copy()
    df_92_scatter = df_92[df_92["Finished Picking At"].dt.date == target_date].copy()

    cap_site_map = {}
    cap_site = None
    if show_capacity:
        if not is_api_configured():
            st.info("CubeAnalytics API not configured — hourly capacity chart unavailable.")
        else:
            cap_site_map = _installation_site_map()
            if cap_site_map:
                site_options = sorted(cap_site_map.keys())
                default_site = _default_capacity_site(cap_site_map, warehouse)
                cap_site = st.selectbox(
                    "CubeAnalytics site for capacity chart",
                    options=site_options,
                    index=site_options.index(default_site) if default_site in site_options else 0,
                    key="prio_cap_site",
                    help="Which CubeAnalytics installation the hourly Bin/User time "
                         "and bins-picked-per-hour data is read from.",
                )

    plan_planned = None
    if plan_file is not None:
        try:
            plan_raw = pd.read_csv(plan_file, sep=";")
            plan_raw["parsed_date"] = pd.to_datetime(
                plan_raw["Date"].str.extract(r"(\w+ \w+ \d+ \d+)")[0], format="%a %b %d %Y"
            )
            plan_day = plan_raw[plan_raw["parsed_date"].dt.date == target_date]
            if not plan_day.empty:
                row = plan_day.iloc[0]
                plan_planned = {}
                for h in range(0, 24):
                    p_col = f"order-planned-{h}"
                    if p_col in row.index and pd.notna(row[p_col]) and str(row[p_col]).strip():
                        plan_planned[h] = float(str(row[p_col]).replace(",", ""))
                    else:
                        plan_planned[h] = 0.0
        except Exception:
            pass

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Warehouse", warehouse)
    col2.metric("Date", str(target_date))
    col3.metric("AutoStore 91", f"{len(df_91_scatter):,}")
    col4.metric("AutoStore 92", f"{len(df_92_scatter):,}")
    st.divider()

    st.markdown("#### AutoStore 91")
    stats_91 = _compute_stats(df_91_scatter)
    if stats_91:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total", f"{stats_91['Total']:,}")
        c2.metric("Same-day", f"{stats_91['Same-day']:,}")
        c3.metric("Next-day", f"{stats_91['Next-day']:,}")
        c4.metric("On Time", f"{stats_91['On Time %']}%")
        c5.metric("Late", f"{stats_91['Late %']}%")
        c6.metric("Median", f"{stats_91['Median same-day (min)']} min")

        fig_91 = _generate_chart(df_91_scatter, 91, warehouse,
                                  full_data=df_91, target_date=target_date, plan_planned=plan_planned)
        st.pyplot(fig_91)
        st.download_button("Download PNG — AS91", data=_fig_to_bytes(fig_91),
                           file_name=f"prio_vs_picking_{warehouse}_as91.png", mime="image/png", key="dl_91")
        plt.close(fig_91)

        if show_capacity and cap_site:
            _render_capacity_section(91, cap_site_map, cap_site, target_date, warehouse)
    else:
        st.warning("No data for AutoStore 91")

    st.divider()
    st.markdown("#### AutoStore 92")
    stats_92 = _compute_stats(df_92_scatter)
    if stats_92:
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total", f"{stats_92['Total']:,}")
        c2.metric("Same-day", f"{stats_92['Same-day']:,}")
        c3.metric("Next-day", f"{stats_92['Next-day']:,}")
        c4.metric("On Time", f"{stats_92['On Time %']}%")
        c5.metric("Late", f"{stats_92['Late %']}%")
        c6.metric("Median", f"{stats_92['Median same-day (min)']} min")

        fig_92 = _generate_chart(df_92_scatter, 92, warehouse,
                                  full_data=df_92, target_date=target_date, plan_planned=plan_planned)
        st.pyplot(fig_92)
        st.download_button("Download PNG — AS92", data=_fig_to_bytes(fig_92),
                           file_name=f"prio_vs_picking_{warehouse}_as92.png", mime="image/png", key="dl_92")
        plt.close(fig_92)

        if show_capacity and cap_site:
            _render_capacity_section(92, cap_site_map, cap_site, target_date, warehouse)
    else:
        st.warning("No data for AutoStore 92")

    if show_comparison and stats_91 and stats_92:
        st.divider()
        st.markdown("#### AS91 vs AS92 Comparison")
        comp = pd.DataFrame({"AutoStore 91": stats_91, "AutoStore 92": stats_92}).T
        st.dataframe(comp, use_container_width=True)

    if show_hourly:
        st.divider()
        st.markdown("#### Hourly Pick Task Distribution")
        df["hour"] = df["Finished Picking At"].dt.hour
        hourly = (
            df.groupby(["hour", df["AutoStore"].str.extract(r"\.(\d{2})", expand=False)])
            .size().unstack(fill_value=0)
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
        st.download_button("Download PNG — Hourly", data=_fig_to_bytes(fig_h),
                           file_name=f"hourly_{warehouse}.png", mime="image/png", key="dl_hourly")
        plt.close(fig_h)
