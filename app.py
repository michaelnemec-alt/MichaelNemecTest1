import streamlit as st

st.set_page_config(
    page_title="UNIFY PIVOT Ready",
    page_icon="📊",
    layout="wide",
)

st.title("📊 UNIFY PIVOT Ready")
st.markdown("---")

st.markdown(
    """
    ### Welcome to UNIFY PIVOT Ready!

    Select an analysis from the **left panel**:

    | Page | Description |
    |------|-------------|
    | **Prio vs Picking** | Scatter plot of picking timeliness vs prioritization time |
    | **UNIFY Pivot Ready** | Convert Unify/CubeAnalytics CSV to pivot-ready format |
    | **Day Evaluation** | Shift decision support — plan vs actual, buffer assessment |
    """
)

col1, col2 = st.columns(2)

with col1:
    st.markdown(
        """
        <div style="background:#e3f2fd; border-radius:12px; padding:24px; min-height:180px">
            <h3>📈 Prio vs Picking</h3>
            <p>Are orders picked on time?</p>
            <ul>
                <li>Scatter plots AS91 + AS92</li>
                <li>On-time / Late statistics</li>
                <li>Hourly distribution</li>
                <li>PNG download</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )

with col2:
    st.markdown(
        """
        <div style="background:#fff3e0; border-radius:12px; padding:24px; min-height:180px">
            <h3>🔄 UNIFY Pivot Ready</h3>
            <p>Convert CSV to pivot-ready format</p>
            <ul>
                <li>Upload multiple files at once</li>
                <li>Weighted averages & open ports</li>
                <li>Download output CSV files</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )

col3, _ = st.columns(2)
with col3:
    st.markdown(
        """
        <div style="background:#e8f5e9; border-radius:12px; padding:24px; min-height:180px">
            <h3>🎯 Day Evaluation</h3>
            <p>Shift decision support tool</p>
            <ul>
                <li>Plan vs Actual scissors indicator</li>
                <li>Pre-pick buffer assessment</li>
                <li>Next-day pre-picking volume</li>
                <li>Capacity recommendations</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")
st.markdown(
    "<div style='text-align:center; color:#888; font-size:0.85em;'>"
    "Created by <strong>Michael Nemec</strong>"
    "</div>",
    unsafe_allow_html=True,
)
