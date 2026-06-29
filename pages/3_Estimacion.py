# pages/3_Estimacion.py — Comparación estimado vs referencia SAG/CAN
import streamlit as st
from components.graficas_estimacion import render_comparacion_estimacion
from components.sidebar_filtros import render_filtros_estimacion
from config import CICLOS, VENTANAS, RENDIMIENTO_REF, COLORES_CICLO

st.set_page_config(
    page_title="Estimación — Observatorio Maíz",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Título ─────────────────────────────────────────────────────────────────────
st.markdown("## ⚖️ Estimación de Rendimiento")
st.markdown(
    "Comparación del rendimiento estimado por el modelo VPM contra el valor de "
    "referencia **SAG/CAN** para cada ventana de predicción (T1, T2, T3)."
)
st.divider()

# ── Filtros en el sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    filtros = render_filtros_estimacion()

ciclo_clave = filtros.get("ciclo", "primera")
ventana     = filtros.get("ventana", "T1")
color_ciclo = COLORES_CICLO.get(ciclo_clave, "#2ecc71")
ref_qq_ha   = RENDIMIENTO_REF.get(ciclo_clave, 45.0)

# ── Tarjetas de referencia ─────────────────────────────────────────────────────
st.markdown("#### Referencia SAG / CAN")
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        label=f"Rendimiento Ref. ({ciclo_clave.title()})",
        value=f"{ref_qq_ha:.1f} qq/ha",
        help="Valor de referencia oficial SAG/CAN para este ciclo.",
    )
with c2:
    st.metric(
        label="Ventana activa",
        value=ventana,
        help="Ventana de predicción seleccionada.",
    )
with c3:
    st.metric(
        label="Ciclo",
        value=ciclo_clave.title(),
        help="Ciclo de siembra analizado.",
    )
with c4:
    st.metric(
        label="Unidad",
        value="qq/ha",
        help="Quintales por hectárea.",
    )

st.divider()

# ── Gráfica de comparación ─────────────────────────────────────────────────────
render_comparacion_estimacion(filtros)
