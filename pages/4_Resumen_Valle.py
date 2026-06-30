# pages/4_Resumen_Valle.py — Producción total agregada y métricas de validación
import streamlit as st
from components.graficas_resumen import render_resumen_valle
from components.sidebar_filtros import render_filtros_resumen
from config import METRICAS_VALIDACION, COLORES_CICLO

# ── Título ─────────────────────────────────────────────────────────────────────
st.markdown("## 📊 Resumen del Valle")
st.markdown(
    "Producción total estimada en quintales para el Valle de Comayagua y "
    "métricas de validación del modelo VPM."
)
st.divider()

# ── Filtros en el sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    filtros = render_filtros_resumen()

# ── Métricas de validación — placeholders ─────────────────────────────────────
st.markdown("#### Métricas de validación del modelo")

col_metricas = st.columns(len(METRICAS_VALIDACION))
_placeholder = {
    "RMSE": ("—", "qq/ha"),
    "MAE":  ("—", "qq/ha"),
    "MAPE": ("—", "%"),
    "R²":   ("—", ""),
}
for col, metrica in zip(col_metricas, METRICAS_VALIDACION):
    valor, unidad = _placeholder[metrica]
    with col:
        st.metric(
            label=metrica,
            value=f"{valor} {unidad}".strip(),
            help=f"Métrica {metrica} calculada sobre las parcelas del ciclo seleccionado.",
        )

st.divider()

# ── Producción total ────────────────────────────────────────────────────────────
st.markdown("#### Producción total estimada")

col_total, col_area = st.columns(2)
with col_total:
    st.metric(
        label="Total estimado",
        value="— qq",
        help="Suma de quintales estimados en todas las parcelas de maíz del ciclo.",
    )
with col_area:
    st.metric(
        label="Área sembrada",
        value="— ha",
        help="Superficie total de parcelas clasificadas como maíz en el ciclo.",
    )

st.divider()

# ── Gráficas ───────────────────────────────────────────────────────────────────
render_resumen_valle(filtros)
