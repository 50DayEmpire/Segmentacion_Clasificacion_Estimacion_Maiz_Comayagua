# pages/4_Resumen_Valle.py — Producción total agregada
import streamlit as st
from components.graficas_resumen import render_resumen_valle
from components.sidebar_filtros import render_filtros_resumen
from utils.queries import cargar_resumen_agregado

# ── Título ─────────────────────────────────────────────────────────────────────
st.markdown("## 📊 Resumen")
st.markdown("Producción total estimada en quintales.")
st.divider()

# ── Filtros en el sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    filtros = render_filtros_resumen()

# ── Cargar datos ───────────────────────────────────────────────────────────────
resumen = cargar_resumen_agregado(
    temporada=filtros["ciclo"],
    ventana=filtros["ventana"],
)

# ── Métricas principales ───────────────────────────────────────────────────────
col_total, col_area, col_rend, col_ref, col_parcelas = st.columns(5)

with col_total:
    st.metric(
        label="Total estimado",
        value=f"{resumen['total_produccion_qq']:,.0f} qq" if resumen["total_produccion_qq"] else "— qq",
        help="Suma de quintales estimados en todas las parcelas del ciclo.",
    )
with col_area:
    st.metric(
        label="Área sembrada",
        value=f"{resumen['area_sembrada_ha']:.1f} ha" if resumen["area_sembrada_ha"] else "— ha",
        help="Superficie total de parcelas (distintas) en el ciclo.",
    )
with col_rend:
    st.metric(
        label="Rendimiento promedio",
        value=f"{resumen['rendimiento_promedio_qq_ha']:.1f} qq/ha" if resumen["rendimiento_promedio_qq_ha"] else "—",
        help="Rendimiento promedio ponderado por parcela.",
    )
with col_ref:
    st.metric(
        label="Referencia SAG/CAN",
        value=f"{resumen['rendimiento_ref_qq_ha']:.1f} qq/ha",
        help="Rendimiento de referencia oficial para el ciclo.",
    )
with col_parcelas:
    st.metric(
        label="Parcelas",
        value=resumen["total_parcelas"],
        help="Número de parcelas distintas con datos en el ciclo.",
    )

col_fin, col_act = st.columns(2)
with col_fin:
    st.metric("Ciclos finalizados", resumen["ciclos_finalizados"])
with col_act:
    st.metric("Ciclos activos", resumen["ciclos_activos"])

st.divider()

# ── Clasificaciones ────────────────────────────────────────────────────────────
if resumen["clasificaciones"]:
    st.markdown("#### Clasificación de cultivos")
    cols = st.columns(len(resumen["clasificaciones"]))
    for col, (label, count) in zip(cols, resumen["clasificaciones"].items()):
        with col:
            st.metric(label=label, value=count)

    st.divider()

# ── Gráficas ───────────────────────────────────────────────────────────────────
render_resumen_valle(filtros, resumen)
