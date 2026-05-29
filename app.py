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
    ### Vítej v UNIFY PIVOT Ready!

    Vyber analýzu v **levém panelu**:

    | Stránka | Popis |
    |---------|-------|
    | **Prio vs Picking** | Scatter plot včasnosti pickování vs prioritizační čas |
    | **UNIFY Pivot Ready** | Konverze Unify/CubeAnalytics CSV do pivot-ready formátu |
    """
)

col1, col2 = st.columns(2)

with col1:
    st.markdown(
        """
        <div style="background:#e3f2fd; border-radius:12px; padding:24px; min-height:180px">
            <h3>📈 Prio vs Picking</h3>
            <p>Jsou objednávky pickované včas?</p>
            <ul>
                <li>Scatter ploty AS91 + AS92</li>
                <li>On-time / Late statistiky</li>
                <li>Hodinová distribuce</li>
                <li>Stažení PNG</li>
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
            <p>Konverze CSV do pivot-ready formátu</p>
            <ul>
                <li>Upload více souborů najednou</li>
                <li>Weighted averages & open ports</li>
                <li>Stažení výstupních CSV</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )
