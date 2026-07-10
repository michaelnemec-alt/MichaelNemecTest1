import streamlit as st
import logging
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("app")
logger.info("=== app.py starting, timestamp=%s ===", time.time())

st.set_page_config(page_title="AutoStore Analytics", page_icon="📊", layout="wide")

st.markdown("""
<style>
[data-testid="stSidebarNav"] { display: none; }

header[data-testid="stHeader"] { background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.block-container { padding-top: 0.5rem; max-width: 1380px; }
[data-testid="stPlotlyChart"] { margin-bottom: -20px; }

section[data-testid="stSidebar"] { background-color: #fafafa; border-right: 1px solid #f0f0f0; }
section[data-testid="stSidebar"] button {
    font-size: 10px !important; padding: 4px 8px !important; min-height: 0 !important;
}

.stat-card {
    background: #fafafa;
    border-radius: 8px;
    padding: 16px 20px;
    border: 1px solid #f0f0f0;
}
.stat-number { font-size: 2em; font-weight: 700; color: #111827; line-height: 1.1; }
.stat-label { font-size: 0.7em; text-transform: uppercase; letter-spacing: 1.5px; color: #9ca3af; margin-bottom: 4px; }

.section-card {
    background: white;
    border: 1px solid #f0f0f0;
    border-radius: 8px;
    padding: 20px;
    margin: 8px 0;
}

.footer { text-align:center; color:#d1d5db; font-size:0.78em; padding:40px 0 10px 0; }
</style>
""", unsafe_allow_html=True)

st.markdown("<p style='font-size:0.85em; color:#9ca3af; margin:0;'>AUTOSTORE</p>", unsafe_allow_html=True)
st.markdown("<h1 style='margin:0 0 12px 0; font-size:1.8em; color:#111827; font-weight:800;'>Analytics</h1>", unsafe_allow_html=True)

PAGES = ["Home", "Reporting & Data Tools *", "System OEE *"]
OEE_VIEWS = ["OEE Overview", "System KPI overview *", "Facility Performance KPI *", "AutoStore system *"]
REPORTING_VIEWS = ["Prio vs Picking", "UNIFY Pivot Ready", "Day Evaluation", "Performance"]
SYSTEM_KPI_VIEWS = ["Availability KPI", "Error & Health Metrics *"]
ERROR_HEALTH_VIEWS = ["Uptime metrics", "Robots", "Ports", "Chargers", "System"]
FACILITY_VIEWS = ["Time to Recover", "Reliability", "Incidents"]
AUTOSTORE_VIEWS = ["Versions of Systems", "Bin overview"]

selected = st.segmented_control(
    "nav",
    options=PAGES,
    default="Home",
    key="nav_selection",
    label_visibility="collapsed",
)

if not selected:
    selected = "Home"

reporting_view = None
oee_view = None
system_view = None
error_health_view = None
facility_view = None
autostore_view = None

if selected == "Reporting & Data Tools *":
    reporting_view = st.segmented_control(
        "reporting_nav",
        options=REPORTING_VIEWS,
        default="Prio vs Picking",
        key="reporting_nav_selection",
        label_visibility="collapsed",
    ) or "Prio vs Picking"

if selected == "System OEE *":
    oee_view = st.segmented_control(
        "oee_nav",
        options=OEE_VIEWS,
        default="OEE Overview",
        key="oee_nav_selection",
        label_visibility="collapsed",
    ) or "OEE Overview"

    if oee_view == "System KPI overview *":
        system_view = st.segmented_control(
            "system_kpi_nav",
            options=SYSTEM_KPI_VIEWS,
            default="Availability KPI",
            key="system_kpi_nav_selection",
            label_visibility="collapsed",
        ) or "Availability KPI"
        if system_view == "Error & Health Metrics *":
            error_health_view = st.segmented_control(
                "error_health_nav",
                options=ERROR_HEALTH_VIEWS,
                default="Uptime metrics",
                key="error_health_nav_selection",
                label_visibility="collapsed",
            ) or "Uptime metrics"

    elif oee_view == "Facility Performance KPI *":
        facility_view = st.segmented_control(
            "facility_nav",
            options=FACILITY_VIEWS,
            default="Time to Recover",
            key="facility_nav_selection",
            label_visibility="collapsed",
        ) or "Time to Recover"

    elif oee_view == "AutoStore system *":
        autostore_view = st.segmented_control(
            "autostore_nav",
            options=AUTOSTORE_VIEWS,
            default="Versions of Systems",
            key="autostore_nav_selection",
            label_visibility="collapsed",
        ) or "Versions of Systems"

st.markdown("<br>", unsafe_allow_html=True)

if selected == "Home":
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown("""<div class="stat-card">
            <div class="stat-label">Sites Monitored</div>
            <div class="stat-number">10</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        st.markdown("""<div class="stat-card">
            <div class="stat-label">Analysis Tools</div>
            <div class="stat-number">4</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        st.markdown("""<div class="stat-card">
            <div class="stat-label">Data Sources</div>
            <div class="stat-number">3</div>
        </div>""", unsafe_allow_html=True)
    with col4:
        st.markdown("""<div class="stat-card">
            <div class="stat-label">API Endpoints</div>
            <div class="stat-number">8</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""<div class="section-card">
            <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; color:#9ca3af; margin-bottom:8px;">Reporting & Data Tools</div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>Prio vs Picking</b><br><span style="color:#6b7280; font-size:0.85em;">Scatter plot of picking timeliness vs prioritization time</span></div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>UNIFY Pivot Ready</b><br><span style="color:#6b7280; font-size:0.85em;">Convert CubeAnalytics CSV to pivot-ready format</span></div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>Day Evaluation</b><br><span style="color:#6b7280; font-size:0.85em;">Shift decision support — plan vs actual, buffer assessment</span></div>
            <div style="padding:8px 0;"><b>Performance</b><br><span style="color:#6b7280; font-size:0.85em;">Wait/waste time & throughput, weekday comparison</span></div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("""<div class="section-card">
            <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; color:#9ca3af; margin-bottom:8px;">System OEE</div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>System KPI overview</b><br><span style="color:#6b7280; font-size:0.85em;">Availability & health per module (System, Robots, Ports, Chargers)</span></div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>Facility Performance KPI</b><br><span style="color:#6b7280; font-size:0.85em;">Time-to-recover, reliability (MTBF/MBBD), incidents</span></div>
            <div style="padding:8px 0;"><b>AutoStore system</b><br><span style="color:#6b7280; font-size:0.85em;">Module versions & bin overview across 10 sites</span></div>
        </div>""", unsafe_allow_html=True)

elif selected == "Reporting & Data Tools *":
    if reporting_view == "Prio vs Picking":
        from views.prio_vs_picking import render
        render()
    elif reporting_view == "UNIFY Pivot Ready":
        from views.unify_pivot_ready import render
        render()
    elif reporting_view == "Day Evaluation":
        from views.day_evaluation import render
        render()
    elif reporting_view == "Performance":
        from views.cube_analytics import render
        render("Performance")

elif selected == "System OEE *":
    if oee_view == "OEE Overview":
        from views.cube_analytics import render
        render("OEE Overview")
    elif oee_view == "System KPI overview *":
        from views.cube_analytics import render
        if system_view == "Availability KPI":
            render("Availability KPI")
        else:
            logger.info("Rendering Error & Health sub-view: %s", error_health_view)
            render(error_health_view or "Uptime metrics")
    elif oee_view == "Facility Performance KPI *":
        from views.cube_analytics import render
        logger.info("Rendering Facility Performance view: %s", facility_view)
        render(facility_view or "Time to Recover")
    elif oee_view == "AutoStore system *":
        from views.cube_analytics import render_autostore
        logger.info("Rendering AutoStore system view: %s", autostore_view or "Versions of Systems")
        render_autostore(autostore_view or "Versions of Systems")

st.markdown("<div class='footer'>Created by <b>Michael Nemec</b></div>", unsafe_allow_html=True)
