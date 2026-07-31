import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import re
import calendar
from datetime import date, timedelta

import picking_store
from snowflake_utils import is_snowflake_configured, get_available_warehouses, query_picking_data
from cubeanalytics_utils import (
    is_api_configured, get_installations, query_port_wait_time,
    query_port_wait_time_daily,
    query_live_jobs, query_live_robots, query_live_robot_battery,
    query_robot_state_hourly,
)


def _generate_chart(data, autostore_num, warehouse_name, hourly_overlay=None,
                    plan_planned=None, ax=None):
    own_fig = ax is None
    if own_fig:
        fig, ax = plt.subplots(figsize=(24, 10), dpi=150)
    else:
        fig = ax.figure
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
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

    if hourly_overlay is not None:
        ax2 = ax.twinx()
        base_date = data["Finished Picking At"].dt.normalize().iloc[0]
        hours = list(range(0, 24))
        x_times = [base_date + pd.Timedelta(hours=h) for h in hours]

        prepick_vals = np.array(hourly_overlay.get("prepick", [0] * 24), dtype=float)
        sameday_vals = np.array(hourly_overlay.get("sameday", [0] * 24), dtype=float)
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

    if own_fig:
        fig.tight_layout()
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


def _compute_overlay(full_df, target_date):
    """Hourly pre-pick / same-day counts (by prioritization hour) for a target day.

    prepick = orders prioritized on target_date but finished picking earlier
    (needs the previous day's rows, hence computed from the full uploaded file);
    sameday = prioritized and finished on target_date. Returned as two 24-slot
    lists so it can be frozen to disk and redrawn without the source rows.
    """
    hours = list(range(24))
    all_target = full_df[full_df["Prioritization Time"].dt.date == target_date]
    prepicked = all_target[all_target["Finished Picking At"].dt.date < target_date]
    sameday = all_target[all_target["Finished Picking At"].dt.date == target_date]
    pc = prepicked.groupby("prio_hour").size()
    sc = sameday.groupby("prio_hour").size()
    return {
        "prepick": [int(pc.get(h, 0)) for h in hours],
        "sameday": [int(sc.get(h, 0)) for h in hours],
    }


def _fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


# AutoStore number -> environment: 91 = non-ambient (Chilled), 92 = Ambient.
_AS_ENV = {91: "Chilled", 92: "Ambient"}
_CAPACITY_CATEGORIES = ["1", "2"]
# Target for average wait time (left axis, seconds).
_CAPACITY_TARGET_SEC = 7.0
# Cap the wait-time (left) axis so outlier hours don't flatten the chart.
_CAPACITY_MAX_SEC = 10.0

# Map a picking-export warehouse code (e.g. "hu.bud2") to a CubeAnalytics site
# (city). Keys are matched against the warehouse code as substrings, so they must
# be specific enough not to collide (e.g. "prg2" vs "prg3"). Warehouses without a
# CubeAnalytics installation (PRG3/Chrášťany, FRA/Bischofsheim) map to a city that
# is not returned by the API, so no capacity data is shown for them.
_WAREHOUSE_CITY_HINTS = {
    "prg2": "Praha",
    "prg3": "Chrášťany",
    "bud": "Biatorbágy",
    "vie": "Vienna",
    "muc": "Garching",
    "gar": "Garching",
    "ber": "Schönefeld",
    "sch": "Schönefeld",
    "fra": "Bischofsheim",
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
    """Resolve the CubeAnalytics site for a warehouse code.

    Returns the mapped city only if it has a CubeAnalytics installation. Returns
    None when the warehouse has no API site (e.g. PRG3/Chrášťany, FRA/Bischofsheim)
    or is unknown, so the caller can skip the capacity overlay instead of guessing.
    """
    wh = (warehouse or "").lower()
    for hint, city in _WAREHOUSE_CITY_HINTS.items():
        if hint in wh:
            return city if city in sites else None
    return None


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


def _bin_presentations_hourly(df_wait, target_date):
    """Total bin presentations per hour (all pick types + categories) for one day.

    This is the full flow through the AutoStore (not just picked cat 1+2), i.e.
    sum of Count across every port/pick_type/category per hour.
    """
    out = [0.0] * 24
    if df_wait is None or df_wait.empty:
        return out
    d = df_wait[df_wait["Timestamp"].dt.date == target_date]
    if d.empty:
        return out
    g = d.groupby(d["Timestamp"].dt.hour)["Count"].sum()
    for h in range(24):
        out[h] = float(g.get(h, 0))
    return out


def _maxcap_hourly(df_wait_window, quantile=0.95):
    """Per-hour peak of daily total bin presentations over the supplied window.

    For each hour-of-day, take the 95th percentile across all days of that day's
    total bin presentations in that hour — the peak throughput envelope, robust
    to single-day outliers.
    """
    out = [0.0] * 24
    if df_wait_window is None or df_wait_window.empty:
        return out
    d = df_wait_window.copy()
    d["_d"] = d["Timestamp"].dt.date
    d["_h"] = d["Timestamp"].dt.hour
    daily = d.groupby(["_d", "_h"])["Count"].sum().reset_index()
    q = daily.groupby("_h")["Count"].quantile(quantile)
    for h in range(24):
        out[h] = float(q.get(h, 0))
    return out


def _theomax_flat(df_wait, target_date, df_robot_hourly, util_min=0.6, q=0.95):
    """Hourly throughput ceiling = peak per-robot productivity x available fleet.

    Per-robot productivity drops off-peak because idle robots queue at ports, so
    the peak rate is the sustainable one: the q-quantile of hourly
    bins / working-robots over hours busy enough (utilisation >= util_min) to
    reflect real throughput. That peak rate is scaled per hour by the robots
    actually available that hour = fleet minus robots kept off work by charging
    (charging_unavailable), since batteries must recharge under load; robots
    down for service/maintenance are NOT excluded (a facility issue, not a
    capacity limit of the system). The line therefore follows the available
    fleet across the day. Returns a 24-slot list (0 when robot-state is
    unavailable that hour).
    """
    out = [0.0] * 24
    if (df_robot_hourly is None or df_robot_hourly.empty
            or df_wait is None or df_wait.empty):
        return out
    w = df_wait.copy()
    w["_d"] = w["Timestamp"].dt.date
    w["_h"] = w["Timestamp"].dt.hour
    bins = w.groupby(["_d", "_h"])["Count"].sum().rename("bins").reset_index()
    r = df_robot_hourly.copy()
    r["_d"] = r["date"].dt.date
    cols = ["working_s", "total_s", "charging_unavailable_s"]
    rob = r.groupby(["_d", "hour"])[cols].sum().reset_index()
    rob = rob.rename(columns={"hour": "_h"})
    m = bins.merge(rob, on=["_d", "_h"], how="inner")
    m = m[(m["total_s"] > 0) & (m["working_s"] > 0)]
    if m.empty:
        return out
    m["util"] = m["working_s"] / m["total_s"]
    m["rate"] = m["bins"] / (m["working_s"] / 3600.0)
    good = m[m["util"] >= util_min]
    if good.empty:
        return out
    peak_rate = float(good["rate"].quantile(q))
    day = rob[rob["_d"] == target_date]
    if day.empty:
        return out
    avail_by_hour = (
        (day["total_s"] - day["charging_unavailable_s"]) / 3600.0
    )
    avail_by_hour.index = day["_h"]
    for h in range(24):
        if h in avail_by_hour.index:
            out[h] = peak_rate * float(avail_by_hour.loc[h])
    return out


def _capacity_arrays(df_wait_day, target_date, df_wait_window=None,
                     df_robot_hourly=None):
    """Bundle every hourly capacity series for one day into 24-slot lists.

    df_wait_day     : single-day (or wider) port-wait data; bin/user time and
                      total bin presentations are read from the target_date slice.
    df_wait_window  : the look-back window ending at target_date, used only for
                      the 95th-percentile peak envelope. This is what gets frozen
                      so the envelope reflects the window as of that day.
    df_robot_hourly : hourly robot-state, used for the flat throughput ceiling
                      (peak per-robot productivity x fleet size).
    """
    _, bin_time, user_time, bins = _capacity_hourly(df_wait_day, target_date)
    total_bins = _bin_presentations_hourly(df_wait_day, target_date)
    maxcap = _maxcap_hourly(df_wait_window) if df_wait_window is not None else [0.0] * 24
    theomax = _theomax_flat(df_wait_day, target_date, df_robot_hourly)
    return {
        "bin_time": bin_time,
        "user_time": user_time,
        "bins": bins,
        "total_bins": total_bins,
        "maxcap": maxcap,
        "theomax": theomax,
    }


def _capacity_kpis(cap):
    """Whole-day AutoStore KPIs from the hourly capacity arrays.

    `utilisation` = average over the day of hourly (total bin presentations /
    theoretical max). The grey line (all bin presentations, every pick type and
    category) is the real load of the AutoStore; the purple ceiling is 100 %.
    We use all presentations (not just picked cat 1+2) because bins that are not
    picked still occupy the system, so picks alone could never reach 100 %.
    Only hours where the ceiling is known (theomax > 0) are averaged.

    `picks_cat12` = whole-day count of picked bins in category 1 + 2 (black line).
    """
    theomax = (cap or {}).get("theomax") or []
    total_bins = (cap or {}).get("total_bins") or []
    bins = (cap or {}).get("bins") or []
    utils = [tot / tm * 100.0
             for tm, tot in zip(theomax, total_bins) if tm and tm > 0]
    if not utils:
        return None
    return {
        "utilisation": sum(utils) / len(utils),
        "picks_cat12": sum(bins),
        "hours": len(utils),
    }


def _daily_cat12_picks(inst_id, start_date, end_date):
    """Whole-day picked-bin counts (category 1+2) per calendar day from the
    port-bin-wait-time daily source (same source as UNIFY Pivot Ready, full
    history). Returns a Series indexed by date."""
    dfd = query_port_wait_time_daily(inst_id, str(start_date), str(end_date))
    if dfd is None or dfd.empty:
        return pd.Series(dtype=float)
    d = dfd[(dfd["pick_type"] == "picks")
            & (dfd["category"].isin(_CAPACITY_CATEGORIES))]
    if d.empty:
        return pd.Series(dtype=float)
    return d.groupby(d["date"].dt.date)["count"].sum()


def _util_for_day(inst_id, day):
    """Whole-day utilisation (grey ÷ purple, averaged over the day) for one
    AutoStore on one day. Pulls that single day's hourly port-wait + robot-state
    (both disk-cached) so the 4-month matrix builds day by day without holding a
    long hourly window in memory. Returns NaN when data is missing."""
    if not inst_id:
        return float("nan")
    try:
        dfw = query_port_wait_time(
            inst_id, str(day), str(day + timedelta(days=1)))
        rob = query_robot_state_hourly(
            inst_id, str(day), str(day + timedelta(days=1)))
    except Exception:
        return float("nan")
    if dfw is None or dfw.empty or rob is None or rob.empty:
        return float("nan")
    cap = _capacity_arrays(dfw, day, df_robot_hourly=rob)
    kpi = _capacity_kpis(cap)
    return kpi["utilisation"] if kpi else float("nan")


def _weekday_kpi_matrix(site_map, site, target_date, months=4, with_util=True):
    """Per-day KPI matrix for the same weekday over the last `months`.

    One row per same-weekday day (e.g. every Friday) that has picking data.
    Columns are paired per AutoStore (91 | 92): whole-day utilisation, whole-day
    cat 1+2 picks and potential lost % (shortfall vs the best same-weekday day,
    which is 100 %). Returns (DataFrame, best_dates) or None.
    """
    insts = {num: site_map.get(site, {}).get(_AS_ENV.get(num))
             for num in (91, 92)}
    if not any(insts.values()):
        return None
    start = target_date - timedelta(days=30 * months)
    end = target_date + timedelta(days=1)
    wd = target_date.weekday()

    picks, best_val, best_date = {}, {}, {}
    for num, inst in insts.items():
        if not inst:
            continue
        s = _daily_cat12_picks(inst, start, end)
        s = s[[d.weekday() == wd for d in s.index]]
        picks[num] = s
        if not s.empty and float(s.max()) > 0:
            best_val[num] = float(s.max())
            best_date[num] = s.idxmax()

    days = sorted(set().union(*[set(s.index) for s in picks.values()])) \
        if picks else []
    if not days:
        return None

    rows = {}
    for d in days:
        row = {}
        for num in (91, 92):
            s = picks.get(num, pd.Series(dtype=float))
            pk = float(s.get(d, float("nan")))
            row[(f"AS{num}", "Picks 1+2")] = pk
            b = best_val.get(num)
            row[(f"AS{num}", "Lost %")] = (
                max(0.0, (1.0 - pk / b) * 100.0)
                if b and pk == pk else float("nan"))
            row[(f"AS{num}", "Util %")] = (
                _util_for_day(insts.get(num), d) if with_util else float("nan"))
        rows[d] = row

    df = pd.DataFrame(
        {d: rows[d] for d in days}).T
    df = df.reindex(columns=pd.MultiIndex.from_tuples(
        [(f"AS{n}", m) for n in (91, 92)
         for m in ("Util %", "Picks 1+2", "Lost %")]))
    df.index = [d.isoformat() for d in days]
    return df, {num: best_date.get(num) for num in (91, 92)}


def _capacity_chart(df_wait, autostore_num, warehouse_name, target_date, site_name, ax=None):
    """Combo chart mirroring 'AS Max capacity utilization':

    blue columns = avg Bin wait time, yellow columns = avg Operator handling time
    (left axis, seconds, capped at _CAPACITY_MAX_SEC), continuous line = bins
    picked per hour (right axis). Pick tasks category 1 + 2 only.
    """
    hours, bin_time, user_time, bins = _capacity_hourly(df_wait, target_date)

    own_fig = ax is None
    if own_fig:
        fig, ax = plt.subplots(figsize=(24, 6), dpi=150)
    else:
        fig = ax.figure
    ax.set_facecolor("#f8f8f8")
    fig.patch.set_facecolor("white")
    ax.grid(True, axis="y", alpha=0.3, color="#cccccc")

    width = 0.42
    x = np.arange(24)
    ax.bar(x - width / 2, bin_time, width, color="#3f76c4",
           label="Average bin wait time")
    ax.bar(x + width / 2, user_time, width, color="#e8c24a",
           label="Average operator handling time")
    ax.axhline(
        _CAPACITY_TARGET_SEC, color="#c0392b", linestyle="--", linewidth=1.6,
        label=f"Target {_CAPACITY_TARGET_SEC:.0f} s",
    )
    ax.set_ylabel("Seconds (avg per bin)", fontsize=13)
    ax.set_xlabel("Hour", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{h:02d}" for h in hours])
    ax.set_ylim(0, _CAPACITY_MAX_SEC)

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

    if own_fig:
        fig.tight_layout()
    return fig


def _load_wait_df(autostore_num, site_map, selected_site, target_date):
    """Fetch port-wait data (UNIFY Pivot Ready source) for the mapped installation."""
    env = _AS_ENV.get(autostore_num)
    inst_id = site_map.get(selected_site, {}).get(env)
    if not inst_id:
        st.info(f"No CubeAnalytics {env} installation found for site '{selected_site}'.")
        return None
    with st.spinner(f"Loading capacity data (AS{autostore_num} / {env})..."):
        try:
            return query_port_wait_time(
                inst_id, str(target_date), str(target_date + timedelta(days=1))
            )
        except Exception as e:
            st.error(f"Capacity data query failed: {e}")
            return None


def _load_maxcap_df(autostore_num, site_map, selected_site, target_date, months=2):
    """Fetch ~2 months of port-wait data to build the peak bin-presentations envelope."""
    env = _AS_ENV.get(autostore_num)
    inst_id = site_map.get(selected_site, {}).get(env)
    if not inst_id:
        return None
    start = target_date - timedelta(days=30 * months)
    with st.spinner(f"Loading {months}-month peak capacity (AS{autostore_num} / {env})..."):
        try:
            return query_port_wait_time(
                inst_id, str(start), str(target_date + timedelta(days=1))
            )
        except Exception as e:
            st.warning(f"Peak-capacity query failed: {e}")
            return None


def _load_robot_hourly_df(autostore_num, site_map, selected_site, target_date):
    """Fetch one day of hourly robot-state for the theoretical-max ceiling."""
    env = _AS_ENV.get(autostore_num)
    inst_id = site_map.get(selected_site, {}).get(env)
    if not inst_id:
        return None
    try:
        return query_robot_state_hourly(
            inst_id, str(target_date), str(target_date + timedelta(days=1))
        )
    except Exception as e:
        st.warning(f"Robot-state query failed (AS{autostore_num}): {e}")
        return None


def _overlay_capacity(ax, cap, base_date):
    """Overlay hourly capacity data onto the scatter axes on their own y-axes.

    `cap` is the precomputed arrays dict from _capacity_arrays (bin_time,
    user_time, bins, total_bins, maxcap), so the same drawing code serves both
    the live path and the frozen-on-disk history path.

    Bin wait / operator handling times (bars, seconds) sit behind the scatter on
    a right-hand seconds axis; bins picked/hour is a line on a further-right axis.
    The same bins axis also carries the grey total bin-presentations/hour line and
    the red peak envelope.
    """
    hours = list(range(24))
    bin_time = cap.get("bin_time", [0.0] * 24)
    user_time = cap.get("user_time", [0.0] * 24)
    bins = cap.get("bins", [0.0] * 24)
    total_bins = cap.get("total_bins")
    maxcap = cap.get("maxcap")
    theomax = cap.get("theomax")
    x_num = mdates.date2num([base_date + pd.Timedelta(hours=h) for h in hours])
    w = 0.34 / 24.0

    # Align a secondary axis so its 0 lands on the scatter's 0-minute line.
    ymin, ymax = ax.get_ylim()
    def _aligned_ylim(top):
        if ymin < 0 < ymax:
            f = (0 - ymin) / (ymax - ymin)
            return (-top * f / (1 - f), top)
        return (0, top)

    ax_sec = ax.twinx()
    ax_sec.spines["right"].set_position(("axes", 1.055))
    ax_sec.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    ax_sec.bar(x_num - w / 2, bin_time, width=w, color="#3f76c4", alpha=0.35,
               label="Bin wait time (s)")
    ax_sec.bar(x_num + w / 2, user_time, width=w, color="#e8c24a", alpha=0.35,
               label="Operator handling time (s)")
    ax_sec.axhline(_CAPACITY_TARGET_SEC, color="#c0392b", linestyle="--",
                   linewidth=1.4, label=f"Target {_CAPACITY_TARGET_SEC:.0f} s")
    ax_sec.set_ylim(*_aligned_ylim(_CAPACITY_MAX_SEC))
    ax_sec.set_ylabel("Time (seconds)", fontsize=13)

    peak = list(bins)
    if total_bins is not None:
        peak += list(total_bins)
    if maxcap is not None:
        peak += list(maxcap)
    if theomax is not None:
        peak += [v for v in theomax if v]
    bins_top = max(peak) * 1.05 if any(peak) else 1.0
    ax_bins = ax.twinx()
    ax_bins.spines["right"].set_position(("axes", 1.11))
    if maxcap is not None and any(maxcap):
        ax_bins.plot(x_num, maxcap, color="#d0342c", linewidth=1.8,
                     label="Peak bin presentations / hour (95th pct, ~2 months to date)")
    if total_bins is not None and any(total_bins):
        ax_bins.plot(x_num, total_bins, color="#9aa0a6", linewidth=1.8,
                     label="Bin presentations / hour (total)")
    if theomax is not None and any(theomax):
        tm = [v if v else float("nan") for v in theomax]
        ax_bins.plot(x_num, tm, color="#7d3cc7", linewidth=1.8,
                     label="Theoretical max / hour (peak productivity x available robots/hour)")
    ax_bins.plot(x_num, bins, color="#111111", linewidth=2, marker="o",
                 markersize=3.5, label="Bins picked / hour (cat 1+2)")
    ax_bins.set_ylim(*_aligned_ylim(bins_top))
    ax_bins.set_ylabel("Bins picked / hour", fontsize=13)

    h1, l1 = ax_sec.get_legend_handles_labels()
    h2, l2 = ax_bins.get_legend_handles_labels()
    ax_sec.legend(h1 + h2, l1 + l2, loc="lower left", fontsize=11, framealpha=0.9)


def _combined_chart(scatter_data, cap, autostore_num, warehouse, hourly_overlay,
                    target_date, plan_planned, site_name):
    """Prio-vs-Picking scatter with hourly capacity data overlaid in one chart.

    `cap` is the precomputed capacity arrays dict; `hourly_overlay` the frozen
    pre-pick / same-day counts.
    """
    fig, ax = plt.subplots(figsize=(26, 11), dpi=150)
    fig.patch.set_facecolor("white")
    _generate_chart(scatter_data, autostore_num, warehouse,
                    hourly_overlay=hourly_overlay, plan_planned=plan_planned, ax=ax)
    base_date = scatter_data["Finished Picking At"].dt.normalize().iloc[0]
    _overlay_capacity(ax, cap, base_date)
    ax.set_title(
        f"Prio vs Picking + capacity — AutoStore {autostore_num} "
        f"({_AS_ENV.get(autostore_num, '')})\n{site_name} | {target_date}",
        fontsize=16, fontweight="bold",
    )
    fig.subplots_adjust(right=0.86)
    return fig


def _parse_df(df_raw):
    """Filter to STANDARD/EXPRESS and add the derived columns the charts use."""
    df = df_raw[df_raw["Type"].isin(["STANDARD", "EXPRESS"])].copy()
    df["Prioritization Time"] = pd.to_datetime(df["Prioritization Time"], errors="coerce")
    df["Finished Picking At"] = pd.to_datetime(df["Finished Picking At"], errors="coerce")
    df = df.dropna(subset=["Prioritization Time", "Finished Picking At"])
    df["diff_minutes"] = (
        (df["Prioritization Time"] - df["Finished Picking At"]).dt.total_seconds() / 60
    )
    df["is_next_day"] = df["Prioritization Time"].dt.date > df["Finished Picking At"].dt.date
    df["prio_hour"] = df["Prioritization Time"].dt.hour
    return df


def _warehouse_of(df):
    parts = df["AutoStore"].iloc[0].split(".")
    return f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else "unknown"


def _parse_plan(plan_file):
    """Parse an optional plan CSV into {date: {hour: planned_orders}}."""
    out = {}
    if plan_file is None:
        return out
    try:
        plan_raw = pd.read_csv(plan_file, sep=";")
        plan_raw["parsed_date"] = pd.to_datetime(
            plan_raw["Date"].str.extract(r"(\w+ \w+ \d+ \d+)")[0], format="%a %b %d %Y"
        )
        for _, row in plan_raw.iterrows():
            d = row["parsed_date"]
            if pd.isna(d):
                continue
            planned = {}
            for h in range(24):
                col = f"order-planned-{h}"
                if col in row.index and pd.notna(row[col]) and str(row[col]).strip():
                    planned[h] = float(str(row[col]).replace(",", ""))
                else:
                    planned[h] = 0.0
            out[d.date()] = planned
    except Exception:
        pass
    return out


def _resolve_cap_site(warehouse, show_capacity):
    """Resolve the CubeAnalytics site for a warehouse; returns (site_map, site).

    site is None when the API is off, no installations are visible, or the
    warehouse has no CubeAnalytics installation (e.g. PRG3, FRA).
    """
    if not show_capacity or not is_api_configured():
        if show_capacity and not is_api_configured():
            st.info("CubeAnalytics API not configured — hourly capacity chart unavailable.")
        return {}, None
    site_map = _installation_site_map()
    if not site_map:
        return site_map, None
    default_site = _default_capacity_site(site_map, warehouse)
    if default_site is None:
        st.info(
            f"CubeAnalytics nemá instalaci pro sklad **{warehouse}** "
            "(např. PRG3/Chrášťany, FRA/Bischofsheim) — hodinová capacity data "
            "(bin presentations, peak) se nevykreslí."
        )
        return site_map, None
    site_options = sorted(site_map.keys())
    site = st.selectbox(
        "CubeAnalytics site for capacity chart",
        options=site_options,
        index=site_options.index(default_site),
        key="prio_cap_site",
        help="Which CubeAnalytics installation the hourly Bin/User time and "
             "bins-picked-per-hour data is read from.",
    )
    return site_map, site


def _capacity_for_day(site_map, site, target_date, big_by_as=None, robot_by_as=None):
    """Compute the frozen capacity arrays per AutoStore for one day, or None.

    big_by_as (optional) supplies a pre-fetched wider window per AS so an ingest
    of many days issues one API query instead of one per day; when absent it
    falls back to per-day queries (used by the live/recalculate paths).
    robot_by_as mirrors big_by_as for hourly robot-state (theoretical-max line).
    """
    out = {}
    for num in (91, 92):
        if big_by_as is not None:
            bw = big_by_as.get(num)
            if bw is None or bw.empty:
                continue
            window = bw[
                (bw["Timestamp"].dt.date > target_date - timedelta(days=60))
                & (bw["Timestamp"].dt.date <= target_date)
            ]
            rob = robot_by_as.get(num) if robot_by_as is not None else None
            out[str(num)] = _capacity_arrays(bw, target_date, window, rob)
        else:
            df_wait = _load_wait_df(num, site_map, site, target_date)
            if df_wait is None:
                continue
            df_win = _load_maxcap_df(num, site_map, site, target_date)
            df_robot = _load_robot_hourly_df(num, site_map, site, target_date)
            out[str(num)] = _capacity_arrays(df_wait, target_date, df_win, df_robot)
    return out or None


def _ingest_upload(df, warehouse, site_map, site, show_capacity, plan_by_date, source):
    """Store every day in the uploaded file except the oldest.

    The oldest day is dropped because its pre-pick (orders finished the previous
    day) isn't in the file, so its overlay would be incomplete. Frozen capacity
    is computed only for days that don't already have it.
    """
    dates = sorted(df["Finished Picking At"].dt.date.unique())
    dropped = dates[0] if dates else None
    store_dates = dates[1:]

    big_by_as = None
    robot_by_as = None
    need_cap = show_capacity and site
    to_freeze = [d for d in store_dates if not picking_store.has_capacity(warehouse, d)]
    if need_cap and to_freeze:
        span_start = min(to_freeze) - timedelta(days=60)
        span_end = max(to_freeze) + timedelta(days=1)
        big_by_as = {}
        robot_by_as = {}
        for num in (91, 92):
            env = _AS_ENV.get(num)
            inst = site_map.get(site, {}).get(env)
            if not inst:
                continue
            with st.spinner(f"Loading capacity window (AS{num})..."):
                try:
                    big_by_as[num] = query_port_wait_time(inst, str(span_start), str(span_end))
                except Exception as e:
                    st.warning(f"Peak-capacity query failed (AS{num}): {e}")
                try:
                    robot_by_as[num] = query_robot_state_hourly(
                        inst, str(min(to_freeze)), str(span_end)
                    )
                except Exception as e:
                    st.warning(f"Robot-state query failed (AS{num}): {e}")

    prog = st.progress(0.0, text="Storing days...")
    for i, d in enumerate(store_dates):
        df_day = df[df["Finished Picking At"].dt.date == d]
        overlay = {
            str(num): _compute_overlay(
                df[df["AutoStore"].str.contains(f".{num}", regex=False)], d
            )
            for num in (91, 92)
        }
        capacity = None
        if need_cap and d in to_freeze:
            capacity = _capacity_for_day(
                site_map, site, d, big_by_as=big_by_as, robot_by_as=robot_by_as
            )
        picking_store.save_day(
            warehouse, d, df_day, overlay, capacity=capacity,
            plan=plan_by_date.get(d), source=source, site=site,
            keep_existing_capacity=True,
        )
        prog.progress((i + 1) / len(store_dates), text=f"Stored {d}")
    prog.empty()
    return store_dates, dropped


def _draw_capacity_kpi_table(site_map, site, target_date):
    """Same-weekday capacity KPI matrix (rows = days, columns paired per AS).

    Rows are every same-weekday day (e.g. all Fridays) in the last 4 months with
    picking data; the viewed day and each AutoStore's best day are highlighted.
    Columns are grouped per AutoStore (91 | 92): whole-day utilisation, whole-day
    cat 1+2 picks and potential lost % vs the best same-weekday day (100 %).
    """
    st.divider()
    weekday = target_date.strftime("%A")
    st.markdown(f"#### AutoStore capacity KPIs — {weekday}s (last 4 months)")
    with st.spinner(f"Building {weekday} KPI matrix "
                    "(utilisation pulls per-day data — first load is slower, "
                    "then served from disk cache)..."):
        res = _weekday_kpi_matrix(site_map, site, target_date)
    if res is None:
        st.info("No comparable same-weekday history for capacity KPIs.")
        return
    df, best_dates = res
    tgt = target_date.isoformat()
    best_rows = {num: (d.isoformat() if d else None)
                 for num, d in best_dates.items()}

    fmt = {}
    for n in (91, 92):
        fmt[(f"AS{n}", "Util %")] = "{:.0f}%"
        fmt[(f"AS{n}", "Lost %")] = "{:.0f}%"
        fmt[(f"AS{n}", "Picks 1+2")] = "{:,.0f}"
    lost_cols = [(f"AS{n}", "Lost %") for n in (91, 92)]

    def _row_style(row):
        out = [""] * len(row)
        if row.name == tgt:
            out = ["background-color:#cfe3ff;font-weight:600"] * len(row)
        for i, col in enumerate(row.index):
            num = 91 if col[0] == "AS91" else 92
            if best_rows.get(num) == row.name and col[0] == f"AS{num}":
                out[i] = (out[i] + ";" if out[i] else "") + \
                    "border:2px solid #2e7d32"
        return out

    sty = (df.style
           .format(fmt, na_rep="–")
           .background_gradient(cmap="Reds", subset=lost_cols, vmin=0, vmax=40)
           .apply(_row_style, axis=1))
    st.dataframe(sty, use_container_width=True,
                 height=min(38 * (len(df) + 2), 640))
    st.caption(
        "Rows = every " + weekday + " in the last 4 months with data; the viewed "
        "day is highlighted (blue) and each AutoStore's best day is boxed (green). "
        "Util % = avg over the day of (all bin presentations ÷ theoretical "
        "max/hour), purple ceiling = 100 %. Lost % = shortfall in cat 1+2 picks "
        "vs that AutoStore's best " + weekday + " (that day = 100 %).")


def _draw_day_view(view, show_comparison, show_hourly, hourly_context_df,
                   site_map=None):
    """Render the metrics, per-AutoStore charts, comparison and hourly chart."""
    df_day = view["df"]
    target_date = view["date"]
    warehouse = view["warehouse"]
    overlay = view.get("overlay") or {}
    capacity = view.get("capacity") or {}
    plan = view.get("plan")
    site = view.get("site") or warehouse
    site_map = site_map or {}
    has_capacity = False

    df_91_scatter = df_day[df_day["AutoStore"].str.contains(".91", regex=False)].copy()
    df_92_scatter = df_day[df_day["AutoStore"].str.contains(".92", regex=False)].copy()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Warehouse", warehouse)
    col2.metric("Date", str(target_date))
    col3.metric("AutoStore 91", f"{len(df_91_scatter):,}")
    col4.metric("AutoStore 92", f"{len(df_92_scatter):,}")
    st.divider()

    stats = {}
    for num, scatter in ((91, df_91_scatter), (92, df_92_scatter)):
        if num == 92:
            st.divider()
        st.markdown(f"#### AutoStore {num}")
        s = _compute_stats(scatter)
        stats[num] = s
        if not s:
            st.warning(f"No data for AutoStore {num}")
            continue
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total", f"{s['Total']:,}")
        c2.metric("Same-day", f"{s['Same-day']:,}")
        c3.metric("Next-day", f"{s['Next-day']:,}")
        c4.metric("On Time", f"{s['On Time %']}%")
        c5.metric("Late", f"{s['Late %']}%")
        c6.metric("Median", f"{s['Median same-day (min)']} min")

        ov = overlay.get(str(num))
        cap = capacity.get(str(num))
        if cap:
            fig = _combined_chart(scatter, cap, num, warehouse, ov,
                                  target_date, plan, site)
        else:
            fig = _generate_chart(scatter, num, warehouse,
                                  hourly_overlay=ov, plan_planned=plan)
        st.pyplot(fig)
        if cap:
            has_capacity = True
        st.download_button(f"Download PNG — AS{num}", data=_fig_to_bytes(fig),
                           file_name=f"prio_vs_picking_{warehouse}_as{num}.png",
                           mime="image/png", key=f"dl_{num}")
        plt.close(fig)

    if has_capacity:
        _draw_capacity_kpi_table(site_map, site, target_date)

    if show_comparison and stats.get(91) and stats.get(92):
        st.divider()
        st.markdown("#### AS91 vs AS92 Comparison")
        comp = pd.DataFrame({"AutoStore 91": stats[91], "AutoStore 92": stats[92]}).T
        st.dataframe(comp, use_container_width=True)

    if show_hourly:
        _draw_hourly_distribution(hourly_context_df, warehouse)


def _draw_hourly_distribution(df, warehouse):
    st.divider()
    st.markdown("#### Hourly Pick Task Distribution")
    df = df.copy()
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


def _date_grid_picker(dates, key_prefix):
    """Month calendar (Mon-first) where days with stored data are shaded and
    clickable and days without data are plain. Returns the selected date."""
    st.html(
        "<style>"
        "div[data-testid='stHorizontalBlock']{gap:0.35rem !important;"
        "margin-bottom:-0.15rem !important}"
        "div[data-testid='stColumn'] button{padding:2px 2px !important;"
        "min-height:0 !important}"
        "div[data-testid='stColumn'] button p{white-space:nowrap !important;"
        "font-size:0.85rem !important;line-height:1.1 !important;margin:0 !important}"
        "</style>")
    available = set(dates)
    sel_key = f"{key_prefix}_sel"
    view_key = f"{key_prefix}_view"
    if st.session_state.get(sel_key) not in available:
        st.session_state[sel_key] = dates[-1]
    selected = st.session_state[sel_key]
    if view_key not in st.session_state:
        st.session_state[view_key] = (selected.year, selected.month)
    vy, vm = st.session_state[view_key]

    # Keep the grid at ~30% width by parking a wide empty spacer column on the
    # right (nesting real columns inside a layout column isn't allowed).
    spacer = 16
    day_w = [1] * 7 + [spacer]
    c_prev, c_lbl, c_next, _ = st.columns([2, 3, 2, spacer])
    if c_prev.button("◀", key=f"{key_prefix}_prev", use_container_width=True):
        st.session_state[view_key] = (vy - 1, 12) if vm == 1 else (vy, vm - 1)
        st.rerun(scope="fragment")
    c_lbl.markdown(
        f"<div style='text-align:center;font-weight:600;padding-top:6px;"
        f"font-size:0.8rem'>{calendar.month_name[vm]} {vy}</div>",
        unsafe_allow_html=True)
    if c_next.button("▶", key=f"{key_prefix}_next", use_container_width=True):
        st.session_state[view_key] = (vy + 1, 1) if vm == 12 else (vy, vm + 1)
        st.rerun(scope="fragment")

    hdr = st.columns(day_w)
    for i, name in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
        hdr[i].markdown(
            f"<div style='text-align:center;color:#888;font-size:0.7rem'>{name}</div>",
            unsafe_allow_html=True)

    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(vy, vm):
        cols = st.columns(day_w)
        for i, day in enumerate(week):
            if day.month != vm:
                cols[i].markdown("&nbsp;", unsafe_allow_html=True)
            elif day in available:
                if cols[i].button(
                        str(day.day), key=f"{key_prefix}_d_{day.isoformat()}",
                        type="primary" if day == selected else "secondary",
                        use_container_width=True):
                    st.session_state[sel_key] = day
                    st.rerun(scope="fragment")
            else:
                cols[i].markdown(
                    f"<div style='text-align:center;color:#ccc;padding:6px 0'>"
                    f"{day.day}</div>", unsafe_allow_html=True)
    return st.session_state[sel_key]


def _live_time_axis(ax, ts, dense=False):
    """Format an x-axis of live timestamps as day+hour, local wall-clock.

    dense=True adds hourly major ticks and 15-minute minor gridlines for a
    fine-grained axis (like the per-robot battery view); otherwise ticks are
    every 4 hours to keep the wider charts readable.
    """
    if dense:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
        ax.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=(15, 30, 45)))
        ax.grid(which="minor", axis="x", alpha=0.15, color="#cccccc", linestyle=":")
        plt.setp(ax.get_xticklabels(), rotation=90, ha="center", fontsize=7)
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    if len(ts) > 1:
        ax.set_xlim(ts.iloc[0], ts.iloc[-1])


def _draw_live_test_section(site_map, site, last_hours=48):
    """AS91 live test charts from the CubeAnalytics live event stream (~48h, 5-min).

    Four stacked charts: (1) robots working vs available, (2) job counts,
    (3) average fleet battery %, and (4) per-robot battery %. This is
    live/rolling data (short retention), so it is independent of the
    uploaded-CSV calendar date.
    """
    st.divider()
    st.markdown("### AutoStore 91 — live test (last 48h, 5-min)")
    st.caption("Test charts from the CubeAnalytics live event stream. This is "
               "rolling live data (only the last ~48 hours are available), so "
               "it is independent of the selected history date. Times are the "
               "installation's local wall-clock.")

    inst_id = site_map.get(site, {}).get(_AS_ENV[91]) if site else None
    if not inst_id:
        st.info(f"No CubeAnalytics {_AS_ENV[91]} (AS91) installation for site "
                f"'{site}' — live test charts unavailable.")
        return

    with st.spinner("Loading live event stream (AS91)..."):
        try:
            jobs = query_live_jobs(inst_id, last_hours)
            robots = query_live_robots(inst_id, last_hours)
            battery = query_live_robot_battery(inst_id, last_hours)
        except Exception as e:
            st.error(f"Live event stream query failed: {e}")
            return

    if robots.empty and jobs.empty:
        st.warning("No live events available for this installation (live data may "
                   "not be enabled).")
        return

    # Chart 1 — robots working vs available.
    if not robots.empty:
        fig1, ax = plt.subplots(figsize=(24, 6), dpi=130)
        fig1.patch.set_facecolor("white")
        ax.set_facecolor("#f8f8f8")
        ax.grid(True, alpha=0.3, color="#cccccc")
        ax.fill_between(robots["ts"], robots["working"], color="#2ca02c",
                        alpha=0.25)
        ax.plot(robots["ts"], robots["working"], color="#2ca02c", linewidth=2,
                label="Robots working")
        ax.plot(robots["ts"], robots["available"], color="#1f77b4", linewidth=2,
                label="Robots available")
        ax.plot(robots["ts"], robots["robots"], color="#999999", linewidth=1.2,
                linestyle="--", label="Robots online (total)")
        ax.set_ylabel("Avg concurrent robots", fontsize=13)
        ax.set_ylim(bottom=0)
        ax.set_title("AS91 — Robots working vs available (live 48h)",
                     fontsize=15, fontweight="bold")
        ax.legend(loc="upper left", fontsize=11, framealpha=0.9)
        _live_time_axis(ax, robots["ts"])
        fig1.tight_layout()
        st.pyplot(fig1)
        st.download_button("Download PNG — AS91 robots (live)",
                           data=_fig_to_bytes(fig1),
                           file_name="as91_live_robots.png", mime="image/png",
                           key="dl_live_robots")
        plt.close(fig1)

    # Chart 2 — job counts.
    if not jobs.empty:
        fig2, ax = plt.subplots(figsize=(24, 6), dpi=130)
        fig2.patch.set_facecolor("white")
        ax.set_facecolor("#f8f8f8")
        ax.grid(True, alpha=0.3, color="#cccccc")
        series = [
            ("total", "#111111", "Total jobs"),
            ("active", "#2ca02c", "Active jobs"),
            ("unique", "#17becf", "Unique jobs"),
            ("total_prepared", "#9467bd", "Prepared jobs"),
            ("unique_prepared", "#c5b0d5", "Unique prepared"),
            ("created", "#ff7f0e", "Created (per 5 min)"),
            ("completed", "#1f77b4", "Completed (per 5 min)"),
            ("updated", "#8c564b", "Updated (per 5 min)"),
            ("deleted", "#d62728", "Deleted (per 5 min)"),
        ]
        for col, color, label in series:
            ax.plot(jobs["ts"], jobs[col], color=color, linewidth=1.6, label=label)
        ax.set_ylabel("# jobs", fontsize=13)
        ax.set_ylim(bottom=0)
        ax.set_title("AS91 — Jobs in the system (live 48h, 5-min)",
                     fontsize=15, fontweight="bold")
        ax.legend(loc="upper left", fontsize=10, framealpha=0.9, ncol=3)
        _live_time_axis(ax, jobs["ts"])
        fig2.tight_layout()
        st.pyplot(fig2)
        st.download_button("Download PNG — AS91 jobs (live)",
                           data=_fig_to_bytes(fig2),
                           file_name="as91_live_jobs.png", mime="image/png",
                           key="dl_live_jobs")
        plt.close(fig2)

    # Chart 3 — average fleet battery %.
    if not robots.empty and robots["battery_avg"].notna().any():
        fig3, ax = plt.subplots(figsize=(24, 5), dpi=130)
        fig3.patch.set_facecolor("white")
        ax.set_facecolor("#f8f8f8")
        ax.grid(True, alpha=0.3, color="#cccccc")
        ax.fill_between(robots["ts"], robots["battery_avg"], color="#e8a33d",
                        alpha=0.2)
        ax.plot(robots["ts"], robots["battery_avg"], color="#e8871e",
                linewidth=2, label="Avg fleet battery %")
        ax.set_ylabel("Battery %", fontsize=13)
        ax.set_ylim(0, 100)
        ax.set_title("AS91 — Average robot fleet battery % (live 48h)",
                     fontsize=15, fontweight="bold")
        ax.legend(loc="upper left", fontsize=11, framealpha=0.9)
        _live_time_axis(ax, robots["ts"])
        fig3.tight_layout()
        st.pyplot(fig3)
        st.download_button("Download PNG — AS91 battery (live)",
                           data=_fig_to_bytes(fig3),
                           file_name="as91_live_battery.png", mime="image/png",
                           key="dl_live_battery")
        plt.close(fig3)

    # Chart 4 — per-robot battery %.
    robot_cols = [c for c in battery.columns if c != "ts"] if not battery.empty else []
    if robot_cols:
        fig4, ax = plt.subplots(figsize=(28, 8), dpi=130)
        fig4.patch.set_facecolor("white")
        ax.set_facecolor("#31333a")
        ax.grid(True, alpha=0.25, color="#666666")
        cmap = plt.get_cmap("tab20")
        for i, col in enumerate(robot_cols):
            ax.plot(battery["ts"], battery[col], linewidth=0.9,
                    color=cmap(i % 20), alpha=0.9)
        ax.set_ylabel("Battery %", fontsize=13)
        ax.set_ylim(0, 105)
        ax.set_title(f"AS91 — Battery level per robot ({len(robot_cols)} robots, "
                     f"live 48h)", fontsize=15, fontweight="bold")
        _live_time_axis(ax, battery["ts"], dense=True)
        fig4.tight_layout()
        st.pyplot(fig4)
        st.download_button("Download PNG — AS91 battery per robot (live)",
                           data=_fig_to_bytes(fig4),
                           file_name="as91_live_battery_per_robot.png",
                           mime="image/png", key="dl_live_battery_robot")
        plt.close(fig4)


def _maybe_live_test(show_live_test, warehouse):
    """Render the AS91 live test section, resolving the CubeAnalytics site from
    the warehouse code. Independent of the capacity overlay and history date."""
    if not show_live_test:
        return
    if not is_api_configured():
        st.info("CubeAnalytics API not configured — AS91 live test unavailable.")
        return
    site_map = _installation_site_map()
    site = _default_capacity_site(site_map, warehouse)
    _draw_live_test_section(site_map, site)


def _render_from_store(warehouse, show_comparison, show_hourly, show_capacity,
                       site_map, site, hourly_context_df=None):
    """Date-pick and render one stored day; offer to recalculate today's peak.

    The picker and the day view live in a fragment so that changing the day
    reruns only this block — not the whole page — which keeps the surrounding
    layout (and the scroll position) stable instead of jumping on every click.
    """
    dates = picking_store.list_dates(warehouse)
    if not dates:
        st.info("No stored days for this warehouse yet.")
        return

    @st.fragment
    def _pick_and_draw():
        st.markdown("**Select target date** — shaded days have stored data")
        target_date = _date_grid_picker(dates, key_prefix=f"prio_cal_{warehouse}")

        if site and target_date == date.today():
            if st.button("Recalculate today's peak", key="prio_recalc",
                         help="Re-pull CubeAnalytics and overwrite only today's "
                              "frozen capacity/peak with the current look-back "
                              "window."):
                day = picking_store.load_day(warehouse, target_date)
                cap = _capacity_for_day(site_map, site, target_date)
                picking_store.save_day(
                    warehouse, target_date, day["df"], day["overlay"],
                    capacity=cap, plan=day["plan"], site=site,
                    keep_existing_capacity=False,
                )
                st.rerun(scope="fragment")

        view = picking_store.load_day(warehouse, target_date)
        if view is None:
            st.warning("Stored day could not be loaded.")
            return
        view["site"] = site or view.get("site")
        ctx = hourly_context_df if hourly_context_df is not None else view["df"]
        _draw_day_view(view, show_comparison, show_hourly, ctx, site_map=site_map)

    _pick_and_draw()


def render():
    sf_available = is_snowflake_configured()

    st.markdown(
        '<style>[data-testid="stMetricValue"]{font-size:1.55rem;}'
        '[data-testid="stMetricLabel"]{font-size:0.8rem;}</style>',
        unsafe_allow_html=True,
    )

    store_whs = picking_store.list_warehouses()

    with st.sidebar:
        st.markdown("#### Prio vs Picking")
        sources = ["CSV Upload"]
        if store_whs:
            sources.append("Saved history")
        if sf_available:
            sources.append("Snowflake")
        default_source = ("Saved history" if "Saved history" in sources
                          else sources[0])
        data_source = st.radio("Data source", sources,
                               index=sources.index(default_source),
                               key="prio_ds")

        uploaded_file = plan_file = hist_wh = None
        sf_warehouse = sf_date_from = sf_date_to = None

        if data_source == "Snowflake":
            warehouses = get_available_warehouses()
            sf_warehouse = st.selectbox("Warehouse", warehouses, key="prio_wh")
            col_f, col_t = st.columns(2)
            with col_f:
                sf_date_from = st.date_input("From", value=date.today() - timedelta(days=2), key="prio_from")
            with col_t:
                sf_date_to = st.date_input("To", value=date.today() - timedelta(days=1), key="prio_to")
        elif data_source == "Saved history":
            hist_wh = st.selectbox("Warehouse (stored)", store_whs, key="prio_hist_wh")
            st.caption("Browsing previously uploaded days from the NAS store.")
        else:
            uploaded_file = st.file_uploader("Upload picking export CSV", type=["csv"],
                                             help="Semicolon-delimited (;) CSV. Days are "
                                                  "stored on the NAS so history stays "
                                                  "browsable without re-uploading.",
                                             key="prio_csv")

        if data_source in ("CSV Upload", "Snowflake"):
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
        show_live_test = st.checkbox(
            "Show AS91 live test (48h)",
            value=False,
            key="prio_live_test",
            help="Adds three test charts for AutoStore 91 from the CubeAnalytics "
                 "live event stream (robots working/available, jobs, fleet "
                 "battery %). Live/rolling data — only the last ~48h, 5-min.",
        )

    if data_source == "Saved history":
        # Frozen capacity is read from disk, so no site picker here. The site is
        # resolved silently only so the today-only "Recalculate peak" button works.
        site_map, site = {}, None
        if show_capacity and is_api_configured():
            site_map = _installation_site_map()
            site = _default_capacity_site(site_map, hist_wh)
        _render_from_store(hist_wh, show_comparison, show_hourly, show_capacity,
                           site_map, site)
        _maybe_live_test(show_live_test, hist_wh)
        return

    if data_source == "Snowflake":
        if not (sf_warehouse and sf_date_from and sf_date_to):
            st.info("Select warehouse and date range in the sidebar.")
            return
        with st.spinner("Loading from Snowflake..."):
            try:
                df_raw = query_picking_data(sf_warehouse, str(sf_date_from), str(sf_date_to))
            except Exception as e:
                st.error(f"Snowflake query failed: {e}")
                return
        if df_raw.empty:
            st.warning("No data found.")
            return
        required = ["AutoStore", "Type", "Prioritization Time", "Finished Picking At"]
        missing = [c for c in required if c not in df_raw.columns]
        if missing:
            st.error(f"Missing columns: **{missing}**")
            return
        df = _parse_df(df_raw)
        warehouse = _warehouse_of(df)
        dates = sorted(df["Finished Picking At"].dt.date.unique())
        target_date = (st.selectbox("Select target date", options=dates,
                                    index=len(dates) - 1, key="prio_target_date")
                       if len(dates) > 1 else dates[0])
        site_map, site = _resolve_cap_site(warehouse, show_capacity)
        plan_by_date = _parse_plan(plan_file)
        capacity = _capacity_for_day(site_map, site, target_date) if (show_capacity and site) else None
        overlay = {
            str(num): _compute_overlay(
                df[df["AutoStore"].str.contains(f".{num}", regex=False)], target_date
            )
            for num in (91, 92)
        }
        view = {
            "warehouse": warehouse, "date": target_date,
            "df": df[df["Finished Picking At"].dt.date == target_date].copy(),
            "overlay": overlay, "capacity": capacity,
            "plan": plan_by_date.get(target_date), "site": site,
        }
        _draw_day_view(view, show_comparison, show_hourly, df, site_map=site_map)
        _maybe_live_test(show_live_test, warehouse)
        return

    # CSV Upload
    if uploaded_file is None:
        st.info("Upload a CSV file in the sidebar to get started.")
        if store_whs:
            st.caption("Tip: switch the data source to **Saved history** to browse "
                       "previously uploaded days.")
        return
    # Ingest each uploaded file only once. Streamlit re-runs the whole script on
    # every widget change (e.g. picking a different date), so without this guard
    # the file would be re-parsed and re-stored on each rerun.
    sig = f"{uploaded_file.name}:{uploaded_file.size}"
    if st.session_state.get("prio_ingested_sig") != sig:
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

        df = _parse_df(df_raw)
        if df.empty:
            st.warning("No STANDARD/EXPRESS rows with valid timestamps in the file.")
            return
        warehouse = _warehouse_of(df)
        site_map, site = _resolve_cap_site(warehouse, show_capacity)
        plan_by_date = _parse_plan(plan_file)

        store_dates, dropped = _ingest_upload(
            df, warehouse, site_map, site, show_capacity, plan_by_date,
            source=uploaded_file.name,
        )
        if not store_dates:
            st.warning(
                f"Nothing stored: the file has only the oldest day (**{dropped}**), "
                "which is always dropped because it has no previous day for pre-pick. "
                "Upload at least two days."
                if dropped is not None else "No storable days in the file."
            )
            return
        msg = f"Stored **{len(store_dates)}** day(s) for **{warehouse}** on the NAS."
        if dropped is not None:
            msg += (f" Oldest day **{dropped}** skipped (no previous day → "
                    "incomplete pre-pick).")
        st.session_state["prio_ingested_sig"] = sig
        st.session_state["prio_ingested_wh"] = warehouse
        st.session_state["prio_ingested_site"] = site
        st.success(msg)
    else:
        warehouse = st.session_state["prio_ingested_wh"]
        site = st.session_state["prio_ingested_site"]
        site_map, _ = _resolve_cap_site(warehouse, show_capacity)

    _render_from_store(warehouse, show_comparison, show_hourly, show_capacity,
                       site_map, site)
    _maybe_live_test(show_live_test, warehouse)
