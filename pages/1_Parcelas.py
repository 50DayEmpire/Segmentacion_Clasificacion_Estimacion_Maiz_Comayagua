# pages/1_Parcelas.py — Vista de mapa interactivo de parcelas
import streamlit as st
from components.mapa_parcelas import render_mapa_parcelas
from components.sidebar_filtros import render_filtros_parcelas
from utils.queries import cargar_parcelas

# ── Título ─────────────────────────────────────────────────────────────────────
st.markdown("## 🗺️ Parcelas")
st.markdown(
    "Mapa interactivo de parcelas agrícolas segmentadas en el Valle de Comayagua. "
    "Colorea por **cultivo clasificado** o por **rendimiento estimado** (qq/ha)."
)
st.divider()

# ── Filtros en el sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    filtros = render_filtros_parcelas()
    st.divider()
    if st.button("🔄 Limpiar caché", use_container_width=True, help="Recarga los datos desde la base de datos"):
        cargar_parcelas.clear()
        st.rerun()

# ── Layout principal: mapa + panel lateral de detalle ─────────────────────────
col_mapa, col_detalle = st.columns([3, 1], gap="medium")

with col_mapa:
    render_mapa_parcelas(filtros)

with col_detalle:
    st.markdown("#### Detalle de parcela")
    st.info(
        "Haz clic sobre una parcela en el mapa para ver su información.",
        icon="👆",
    )

    st.markdown("---")
    st.markdown("##### Leyenda")

    modo = filtros.get("modo_color", "cultivo")

    if modo == "cultivo":
        from config import COLORES_CULTIVO
        for cultivo, color in COLORES_CULTIVO.items():
            etiqueta = cultivo.replace("_", " ").title()
            st.markdown(
                f"""
                <div style='display:flex; align-items:center; gap:.6rem;
                            padding:.2rem 0;'>
                    <div style='width:14px; height:14px; border-radius:3px;
                                background:{color}; flex-shrink:0;'></div>
                    <span style='font-size:.85rem;'>{etiqueta}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            """
            <div style='display:flex; justify-content:space-between;
                        font-size:.8rem; color:#95a5a6; margin-bottom:.3rem;'>
                <span>0 qq/ha</span><span>120 qq/ha</span>
            </div>
            <div style='height:14px; border-radius:4px;
                        background:linear-gradient(to right, #2c3e50, #2ecc71);'>
            </div>
            """,
            unsafe_allow_html=True,
        )
