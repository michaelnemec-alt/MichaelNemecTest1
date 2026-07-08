import streamlit as st
import streamlit.components.v1 as components
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

PAGES = ["Home", "Prio vs Picking", "UNIFY Pivot Ready", "Day Evaluation", "CUBE Analytics *"]
CUBE_VIEWS = ["Overview & Health", "Error & Health Metrics", "Performance", "Battery & Robots", "Health Index *"]

selected = st.segmented_control(
    "nav",
    options=PAGES,
    default="Home",
    key="nav_selection",
    label_visibility="collapsed",
)

if not selected:
    selected = "Home"

if selected == "CUBE Analytics *":
    cube_view = st.segmented_control(
        "cube_nav",
        options=CUBE_VIEWS,
        default="Overview & Health",
        key="cube_nav_selection",
        label_visibility="collapsed",
    )
    if not cube_view:
        cube_view = "Overview & Health"

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
            <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; color:#9ca3af; margin-bottom:8px;">Analysis Tools</div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>Prio vs Picking</b><br><span style="color:#6b7280; font-size:0.85em;">Scatter plot of picking timeliness vs prioritization time</span></div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>UNIFY Pivot Ready</b><br><span style="color:#6b7280; font-size:0.85em;">Convert CubeAnalytics CSV to pivot-ready format</span></div>
            <div style="padding:8px 0;"><b>Day Evaluation</b><br><span style="color:#6b7280; font-size:0.85em;">Shift decision support — plan vs actual, buffer assessment</span></div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("""<div class="section-card">
            <div style="font-size:0.7em; text-transform:uppercase; letter-spacing:1px; color:#9ca3af; margin-bottom:8px;">System Monitoring</div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><b>CUBE Analytics</b><br><span style="color:#6b7280; font-size:0.85em;">System health, uptime, performance across all sites</span></div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><span style="color:#6b7280; font-size:0.85em;">5 dashboard views with interactive Plotly charts</span></div>
            <div style="padding:8px 0; border-bottom:1px solid #f5f5f5;"><span style="color:#6b7280; font-size:0.85em;">Data sources: CSV, CubeAnalytics API, Snowflake</span></div>
            <div style="padding:8px 0;"><span style="color:#6b7280; font-size:0.85em;">10 AutoStore sites across 5 locations</span></div>
        </div>""", unsafe_allow_html=True)

elif selected == "Prio vs Picking":
    from views.prio_vs_picking import render
    render()

elif selected == "UNIFY Pivot Ready":
    from views.unify_pivot_ready import render
    render()

elif selected == "Day Evaluation":
    from views.day_evaluation import render
    render()

elif selected == "CUBE Analytics *":
    from views.cube_analytics import render
    logger.info("Rendering CUBE Analytics view: %s", cube_view or "Overview & Health")
    render(cube_view or "Overview & Health")

st.markdown("<div class='footer'>Created by <b>Michael Nemec</b></div>", unsafe_allow_html=True)


def _inject_robot_rider_runner():
    """Replace Streamlit's default running-man status icon with an animated
    person riding the AutoStore robot (wheels spin, rider bobs, arm swings).
    Keeps the 'RUNNING...' text untouched."""
    svg = (
        '<svg class="robot-rider" viewBox="0 0 48 30" xmlns="http://www.w3.org/2000/svg">'
        # rider (bobs), sitting on top of the robot
        '<g class="rr-rider">'
        '<circle cx="24" cy="4" r="2.2" fill="#1F3864"/>'
        '<path d="M24 6.2 L23 11" stroke="#1F3864" stroke-width="1.8" stroke-linecap="round"/>'
        '<path d="M23 11 L27 11 L29 14" stroke="#1F3864" stroke-width="1.8" stroke-linecap="round" fill="none"/>'
        '<line class="rr-arm" x1="23.5" y1="7" x2="28.5" y2="9" stroke="#C5B200" stroke-width="1.8" stroke-linecap="round"/>'
        '</g>'
        # robot body + overhang bar + left mast
        '<rect x="9" y="12" width="22" height="8" rx="1.6" fill="#5B9BD5"/>'
        '<rect x="7" y="10" width="26" height="2.6" rx="1.3" fill="#1F3864"/>'
        '<rect x="7" y="5.5" width="2.4" height="5" rx="1" fill="#1F3864"/>'
        # wheels (spin)
        '<g class="rr-wheel"><circle cx="14" cy="22.5" r="3.4" fill="#2E2E2E"/>'
        '<circle cx="14" cy="22.5" r="1.2" fill="#e8edf3"/>'
        '<line x1="14" y1="19.4" x2="14" y2="25.6" stroke="#e8edf3" stroke-width="0.7"/></g>'
        '<g class="rr-wheel rr-wheel2"><circle cx="26" cy="22.5" r="3.4" fill="#2E2E2E"/>'
        '<circle cx="26" cy="22.5" r="1.2" fill="#e8edf3"/>'
        '<line x1="26" y1="19.4" x2="26" y2="25.6" stroke="#e8edf3" stroke-width="0.7"/></g>'
        '</svg>'
    )
    components.html(
        """
<script>
try {
const doc = window.parent.document;
const STYLE_ID = "robot-rider-style";
if (!doc.getElementById(STYLE_ID)) {
  const s = doc.createElement("style");
  s.id = STYLE_ID;
  s.textContent = `
    [data-testid="stStatusWidget"] svg:not(.robot-rider),
    [data-testid="stStatusWidget"] i { display:none !important; }
    .robot-rider { width:34px; height:22px; margin-right:4px; flex:0 0 auto; }
    .rr-wheel { transform-box: fill-box; transform-origin: center; animation: rr-spin 0.65s linear infinite; }
    .rr-wheel2 { animation-delay: -0.15s; }
    .rr-rider { transform-box: fill-box; transform-origin: 24px 12px; animation: rr-bob 0.6s ease-in-out infinite; }
    .rr-arm { transform-box: fill-box; transform-origin: 23.5px 7px; animation: rr-arm 0.6s ease-in-out infinite; }
    @keyframes rr-spin { to { transform: rotate(360deg); } }
    @keyframes rr-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-1.7px); } }
    @keyframes rr-arm { 0%,100% { transform: rotate(-20deg); } 50% { transform: rotate(16deg); } }
  `;
  doc.head.appendChild(s);
}
const SVG = `%SVG%`;
function inject() {
  const w = doc.querySelector('[data-testid="stStatusWidget"]');
  if (w && !w.querySelector('.robot-rider')) {
    w.insertAdjacentHTML('afterbegin', SVG);
  }
}
setInterval(inject, 250);
inject();
} catch (e) { /* sandbox may block parent access; leave default icon */ }
</script>
""".replace("%SVG%", svg),
        height=0,
    )


_inject_robot_rider_runner()
