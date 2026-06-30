# pages/2_Series_Temporales.py — Curvas EVI, LSWI y GPP por parcela
import streamlit as st
from components.graficas_series import render_series_temporales
from components.sidebar_filtros import render_filtros_series

# ── Título ─────────────────────────────────────────────────────────────────────
st.markdown("## 📈 Series Temporales")
st.markdown(
    "Curvas de índices espectrales y productividad por parcela. "
    "Los marcadores **SOS** y **POS** delimitan el período vegetativo activo."
)
st.divider()

# ── Filtros en el sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    filtros = render_filtros_series()

# ── Selector de parcela ────────────────────────────────────────────────────────
st.markdown("#### Selección de parcela")
col_sel, col_info = st.columns([2, 3], gap="medium")

with col_sel:
    parcela_id = st.selectbox(
        "ID de parcela",
        options=["— Selecciona una parcela —"],
        help="Selecciona el identificador de la parcela a visualizar.",
    )

with col_info:
    if parcela_id and parcela_id != "— Selecciona una parcela —":
        st.markdown("Información de la parcela seleccionada.")
    else:
        st.info(
            "Selecciona una parcela para ver sus series temporales.",
            icon="ℹ️",
        )

st.divider()

# ── Gráficas ───────────────────────────────────────────────────────────────────
render_series_temporales(parcela_id, filtros)
