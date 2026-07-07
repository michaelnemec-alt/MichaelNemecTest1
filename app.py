import streamlit as st

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

PAGES = ["Home", "Prio vs Picking", "UNIFY Pivot Ready", "Day Evaluation", "CUBE Analytics"]
CUBE_VIEWS = ["Overview & Health", "Error & Health Metrics", "Performance", "Battery & Robots", "Health Index"]

selected = st.segmented_control(
    "nav",
    options=PAGES,
    default="Home",
    key="nav_selection",
    label_visibility="collapsed",
)

if not selected:
    selected = "Home"

if selected == "CUBE Analytics":
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

elif selected == "CUBE Analytics":
    from views.cube_analytics import render
    render(cube_view)

st.markdown("<div class='footer'>Created by <b>Michael Nemec</b></div>", unsafe_allow_html=True)
