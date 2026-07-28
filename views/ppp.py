import io
import math
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import streamlit as st

from cubeanalytics_utils import (
    is_api_configured,
    get_installations,
    query_bin_presentations,
    query_installation_data,
)

# Economics defaults (same assumptions as the SIP-meeting PPP analysis).
_DEFAULT_CPP = 0.09          # € per pick if billed pay-per-pick
_DEFAULT_R5_RENT = 705       # € per R5 robot per month (current flat rent)

# R5 Pro / R5+ Pro monthly fee per robot and fleet consolidation uplift, curated
# per site from the earlier proposal. Keyed by the parenthesis-free short name.
_PRO_FEE = {
    "Schönefeld Ambient": 744, "Schönefeld Chilled": 744,
    "Vienna Ambient": 913, "Vienna Chilled": 811,
    "Biatorbágy Ambient": 913, "Biatorbágy Chilled": 913,
}
_PRO_TYPE = {
    "Schönefeld Ambient": "R5 Pro", "Schönefeld Chilled": "R5 Pro",
    "Vienna Ambient": "R5+ Pro", "Vienna Chilled": "R5 Pro",
    "Biatorbágy Ambient": "R5+ Pro", "Biatorbágy Chilled": "R5+ Pro",
}
_UPLIFT = {"Ambient": 0.25, "Chilled": 0.40}

# Commercial model per site: "PPP" (pay-per-pick billing), "Per module" (robots ×
# monthly rent) or "CAPEX" (bought outright). Keyed by parenthesis-free short name.
_SITE_MODEL = {
    "Garching Ambient": "CAPEX",
    "Garching Chilled": "CAPEX",
    "Praha Ambient": "PPP",
    "Praha Chilled": "PPP",
    "Schönefeld Ambient": "PPP",
    "Schönefeld Chilled": "PPP",
    "Vienna Ambient": "PPP",
    "Vienna Chilled": "PPP",
    "Biatorbágy Ambient": "Per module",
    "Biatorbágy Chilled": "Per module",
}

GREEN = "#00A651"
GREY = "#888888"
BLUE = "#1F6FEB"
RED = "#C0392B"


def _short_site(name):
    s = re.sub(r"^\d+-", "", str(name))
    s = re.sub(r"^Rohlik-", "", s)
    return s


def _norm_site(name):
    """Parenthesis-free short site name, e.g. 'Praha Ambient'."""
    return _short_site(name).replace("(", "").replace(")", "").strip()


def _env_of(name):
    return "Ambient" if "Ambient" in name else ("Chilled" if "Chilled" in name else "")


def _site_model(norm):
    return _SITE_MODEL.get(norm, "TBD")


def _asset_counts(inst_id, date_to):
    """Latest-snapshot robot and port counts from the installation-data endpoint."""
    start = date_to - timedelta(days=14)
    df = query_installation_data(inst_id, str(start), str(date_to + timedelta(days=1)))
    robots = ports = 0
    if df is not None and not df.empty:
        for group, key in (("robot", "robots"), ("port", "ports")):
            sub = df[df["group"] == group]
            if sub.empty:
                continue
            last = sub[sub["date"] == sub["date"].max()]
            total = int(last["count"].sum())
            if key == "robots":
                robots = total
            else:
                ports = total
    return robots, ports


def _euro_k(x, _):
    return f"€{x / 1000:.0f}k"


def _econ_chart(months, mlab, picks_by_month, cpp, rent, pro_rent, pro_type, site_name):
    y = [picks_by_month.get(m, 0) * cpp for m in months]
    xs = [i for i, v in enumerate(y) if v > 0]
    ys = [y[i] for i in xs]

    fig, ax = plt.subplots(figsize=(12, 5.2), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f8f8f8")
    ax.grid(alpha=0.25)

    ax.plot(xs, ys, "-o", color=GREEN, lw=2.2, ms=5,
            label="If we paid €0.09/pick (picks × 9c)", zorder=3)
    ax.axhline(rent, color=GREY, ls="--", lw=1.9,
               label=f"Current R5 rent €{rent:,.0f}")
    ax.fill_between(xs, ys, rent, where=[v >= rent for v in ys],
                    color=GREEN, alpha=0.12, interpolate=True)
    ax.fill_between(xs, ys, rent, where=[v < rent for v in ys],
                    color=RED, alpha=0.12, interpolate=True)
    if pro_rent:
        ax.axhline(pro_rent, color=BLUE, ls=":", lw=1.9,
                   label=f"{pro_type} rent €{pro_rent:,.0f}")

    avg = sum(ys) / len(ys) if ys else 0
    ptxt = f"{pro_type} €{pro_rent / 1000:.0f}k" if pro_rent else "Pro TBD"
    ax.set_title(
        f"Pay-per-pick spend vs R5 rent vs R5 Pro rent — {site_name}\n"
        f"R5 €{rent / 1000:.0f}k · {ptxt} · PPP €{avg / 1000:.0f}k",
        fontsize=13, weight="bold",
    )
    ax.yaxis.set_major_formatter(FuncFormatter(_euro_k))
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels(mlab, fontsize=9)
    ax.margins(y=0.18)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
    fig.text(
        0.5, -0.02,
        "Green shading = paying per pick would cost MORE than flat rent (rent is the "
        "better deal); red = per-pick would be cheaper. Picks from AutoStore CubeAnalytics.",
        ha="center", fontsize=9, color="#555555",
    )
    fig.tight_layout()
    return fig


def _throughput_chart(ser, granularity, metric_label, site_name):
    fig, ax = plt.subplots(figsize=(12, 4.6), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f8f8f8")
    ax.grid(alpha=0.25)
    ax.plot(range(len(ser)), ser.values, "-o", color=BLUE, lw=2, ms=4)
    ax.set_title(f"{metric_label} per {granularity.lower()} — {site_name}",
                 fontsize=13, weight="bold")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x / 1000:.0f}k"))
    step = max(1, len(ser) // 16)
    ax.set_xticks(range(0, len(ser), step))
    ax.set_xticklabels([str(i) for i in ser.index[::step]], fontsize=8, rotation=45, ha="right")
    ax.margins(y=0.15)
    fig.tight_layout()
    return fig


def render():
    st.markdown(
        '<style>[data-testid="stMetricValue"]{font-size:1.4rem;}'
        '[data-testid="stMetricLabel"]{font-size:0.8rem;}</style>',
        unsafe_allow_html=True,
    )
    st.markdown("#### PPP — Pay Per Pick")
    st.caption(
        "Compares pay-per-pick spend (bin presentations picked × €/pick) against the "
        "current monthly R5 flat rent and the R5 Pro / R5+ Pro rent, per site."
    )

    if not is_api_configured():
        st.info("CubeAnalytics API not configured — PPP page unavailable.")
        return

    try:
        installations = get_installations()
    except Exception as e:
        st.error(f"Failed to fetch installations: {e}")
        return
    if not installations:
        st.warning("No sites available.")
        return

    name_to_id = {inst["name"]: inst["id"] for inst in installations}
    site_names = sorted(name_to_id)

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        selected_site = st.selectbox("Site", site_names, index=0,
                                     key="ppp_site", format_func=_short_site)
    with c2:
        granularity = st.selectbox("Granularity", ["Day", "Week", "Month"],
                                   index=2, key="ppp_gran")
    with c3:
        metric = st.selectbox("Throughput metric",
                              ["Picks", "Total bin presentations"],
                              index=0, key="ppp_metric")

    # Half-page table row (filled once figures are computed): left = selected-site
    # OPEX table, right = overview of all sites and their commercial model.
    table_area = st.container()

    today = date.today()
    default_from = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
    d1, d2, d3, d4 = st.columns(4)
    with d1:
        date_from = st.date_input("From", value=default_from, key="ppp_from")
    with d2:
        date_to = st.date_input("To", value=today, key="ppp_to")
    with d3:
        cpp = st.number_input("€ / pick", value=_DEFAULT_CPP, step=0.01,
                              format="%.2f", key="ppp_cpp")
    with d4:
        r5_rent = st.number_input("€ / R5 robot / month", value=_DEFAULT_R5_RENT,
                                  step=5, key="ppp_r5")

    inst_id = name_to_id[selected_site]
    short = _short_site(selected_site)
    norm = _norm_site(selected_site)
    env = _env_of(selected_site)

    with st.spinner("Loading asset counts..."):
        robots, ports = _asset_counts(inst_id, date_to)
    with st.spinner("Loading bin presentations..."):
        bp = query_bin_presentations(inst_id, str(date_from), str(date_to + timedelta(days=1)))

    if bp is None or bp.empty:
        st.warning("No bin-presentation data returned for this site and period.")
        return

    monthly_rent = robots * r5_rent

    add_str = st.text_input(
        "Add robots (scenario) — comma-separated counts",
        value="10, 20, 23, 26, 27", key="ppp_add",
        help="Extra monthly rent and new fleet size if you add these robots (× R5 rent).",
    )
    adds = []
    for tok in re.split(r"[,;\s]+", add_str.strip()):
        if tok.isdigit():
            adds.append(int(tok))

    pro_fee = _PRO_FEE.get(norm, 0)
    pro_type = _PRO_TYPE.get(norm, "Pro")
    pro_rent = 0
    if pro_fee and robots:
        uplift = _UPLIFT.get(env, 0.25)
        pro_rent = math.ceil(robots / (1 + uplift)) * pro_fee

    bp["month"] = bp["date"].dt.strftime("%Y-%m")
    picks_by_month = bp.groupby("month")["picks"].sum().to_dict()
    avg_month_picks = np.mean(list(picks_by_month.values())) if picks_by_month else 0
    ppp_month = avg_month_picks * cpp

    st.divider()
    m1, m2, m3, m5, m6 = st.columns(5)
    m1.metric("Robots", f"{robots:,}")
    m2.metric("Ports", f"{ports:,}")
    m3.metric("R5 flat rent / month", f"€{monthly_rent:,.0f}")
    m5.metric("Avg picks / month", f"{avg_month_picks:,.0f}")
    m6.metric("PPP @ €/pick / month", f"€{ppp_month:,.0f}")

    opex = pd.DataFrame(
        {
            "Metric": [
                "Commercial model", "Robots", "Ports", "R5 rent / robot / month",
                "R5 flat rent / month (robots × rent)",
                "Avg picks / month", f"PPP @ €{cpp:.2f}/pick / month",
            ],
            "Value": [
                _site_model(norm), f"{robots:,}", f"{ports:,}", f"€{r5_rent:,.0f}",
                f"€{monthly_rent:,.0f}",
                f"{avg_month_picks:,.0f}", f"€{ppp_month:,.0f}",
            ],
        }
    )
    add_scenario = pd.DataFrame(
        [
            {
                "Added robots": n,
                "New fleet": robots + n,
                "New R5 flat rent / month": f"€{(robots + n) * r5_rent:,.0f}",
                "Extra € / month": f"€{n * r5_rent:,.0f}",
            }
            for n in adds
        ]
    )
    overview = pd.DataFrame(
        [
            {
                "Site": _short_site(n),
                "Environment": _env_of(n),
                "Commercial model": _site_model(_norm_site(n)),
            }
            for n in site_names
        ]
    ).sort_values(["Site"]).reset_index(drop=True)

    with table_area:
        left, right = st.columns(2)
        with left:
            st.markdown(f"##### {short} — OPEX")
            st.dataframe(opex, use_container_width=True, hide_index=True)
        with right:
            st.markdown("##### Site overview — commercial model")
            st.dataframe(overview, use_container_width=True, hide_index=True)

    if not add_scenario.empty:
        st.markdown("##### Add-robots cost scenario")
        st.dataframe(add_scenario, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("##### Pay-per-pick vs monthly robot rent")
    months = sorted(picks_by_month)
    mlab = [pd.to_datetime(m + "-01").strftime("%b %y") for m in months]
    fig_econ = _econ_chart(months, mlab, picks_by_month, cpp, monthly_rent,
                           pro_rent, pro_type, short)
    st.pyplot(fig_econ)
    plt.close(fig_econ)

    st.divider()
    metric_col = "picks" if metric == "Picks" else "bin_presentations"
    metric_label = "Bin presentations picked" if metric == "Picks" else "Total bin presentations"
    ts = bp[["date", metric_col]].copy().set_index("date").sort_index()
    if granularity == "Day":
        ser = ts[metric_col]
        ser.index = ser.index.strftime("%Y-%m-%d")
    elif granularity == "Week":
        ser = ts[metric_col].resample("W-MON").sum()
        ser.index = ser.index.strftime("%Y-%m-%d")
    else:
        ser = ts[metric_col].resample("MS").sum()
        ser.index = ser.index.strftime("%Y-%m")

    st.markdown(f"##### {metric_label} per {granularity.lower()}")
    fig_tp = _throughput_chart(ser, granularity, metric_label, short)
    st.pyplot(fig_tp)
    plt.close(fig_tp)

    out = ser.rename(metric_col).reset_index()
    out.columns = [granularity, metric_label]
    if metric == "Picks":
        out[f"PPP @ €{cpp:.2f}"] = (ser.values * cpp).round(2)
    st.dataframe(out, use_container_width=True, hide_index=True)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name="PPP")
        opex.to_excel(writer, index=False, sheet_name="OPEX")
        overview.to_excel(writer, index=False, sheet_name="Site overview")
        if not add_scenario.empty:
            add_scenario.to_excel(writer, index=False, sheet_name="Add robots")
    st.download_button(
        "Download PPP data (XLSX)", data=buf.getvalue(),
        file_name=f"ppp_{norm.replace(' ', '_')}_{granularity.lower()}_{date_from}_{date_to}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="ppp_dl",
    )
