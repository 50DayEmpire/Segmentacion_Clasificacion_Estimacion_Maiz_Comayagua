# pages/2_Series_Temporales.py — Curvas EVI, LSWI y GPP por parcela
import streamlit as st
from components.graficas_series import render_series_temporales
from components.sidebar_filtros import render_filtros_series
from utils.queries import cargar_lista_parcelas, cargar_parcelas, cargar_datos_series

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
    parcelas = cargar_lista_parcelas()
    placeholder = "— Selecciona una parcela —"
    opciones = [placeholder] + [str(p) for p in parcelas]

    # Auto-selección desde session_state (navegación desde mapa de Parcelas)
    nav_pid = st.session_state.pop("parcela_series", None)
    if nav_pid is None:
        nav_pid = st.query_params.get("parcela_id")
    indice_inicial = 0
    if nav_pid is not None:
        try:
            ps = str(int(nav_pid))
            if ps in opciones:
                indice_inicial = opciones.index(ps)
        except (ValueError, TypeError):
            pass

    parcela_str = st.selectbox(
        "ID de parcela",
        options=opciones,
        index=indice_inicial,
        help="Selecciona el identificador de la parcela a visualizar.",
    )
    parcela_id = None if parcela_str == placeholder else int(parcela_str)

with col_info:
    if parcela_id is not None:
        try:
            gdf = cargar_parcelas()
            fila = gdf[gdf["id_parcela"] == parcela_id]
            if not fila.empty:
                r = fila.iloc[0]
                area_ha = r.get("area_ha")
                area_m2 = r.get("area_m2")
                cols = []
                if area_ha is not None:
                    cols.append(st.metric("Área (ha)", f"{area_ha:.4f}"))
                if area_m2 is not None:
                    cols.append(st.metric("Área (m²)", f"{area_m2:.2f}"))
        except Exception:
            pass
    else:
        st.info(
            "Selecciona una parcela para ver sus series temporales.",
            icon="ℹ️",
        )

st.divider()

# ── Gráficas ───────────────────────────────────────────────────────────────────
datos_series = cargar_datos_series(parcela_id) if parcela_id is not None else None
render_series_temporales(datos_series, filtros, parcela_id)
