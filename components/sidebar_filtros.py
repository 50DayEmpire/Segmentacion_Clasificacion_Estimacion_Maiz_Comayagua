# components/sidebar_filtros.py — Controles del sidebar por vista
"""
Cada función render_filtros_* construye los controles del sidebar
propios de su vista y retorna un dict con los valores seleccionados.
No contiene lógica de consulta ni de renderizado de gráficas.
"""
import streamlit as st
from config import CICLOS, VENTANAS, COLORES_CICLO


def _encabezado_sidebar() -> None:
    """Cabecera común del sidebar con logo y título."""
    st.markdown(
        """
        <div style='text-align:center; padding:.5rem 0 1rem;'>
            <div style='font-size:2.2rem;'>🌽</div>
            <div style='font-weight:700; font-size:1rem;'>Observatorio Maíz</div>
            <div style='color:#95a5a6; font-size:.75rem;'>Valle de Comayagua</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()


def render_filtros_parcelas() -> dict:
    """
    Filtros para la vista Parcelas.

    Retorna
    -------
    dict con claves:
        ciclo       : str  — clave interna del ciclo ('primera' | 'postrera')
        ventana     : str  — ventana de predicción ('T1' | 'T2' | 'T3')
        modo_color  : str  — 'cultivo' | 'rendimiento'
    """
    _encabezado_sidebar()
    st.markdown("### 🗺️ Filtros — Parcelas")

    etiqueta_ciclo = st.selectbox(
        "Ciclo de siembra",
        options=list(CICLOS.keys()),
        help="Filtra las parcelas por ciclo productivo.",
    )
    ciclo = CICLOS[etiqueta_ciclo]

    ventana = st.select_slider(
        "Ventana de predicción",
        options=VENTANAS,
        value="T1",
        help="T1 = inicio, T2 = floración, T3 = llenado de grano.",
    )

    st.markdown("---")
    modo_color = st.radio(
        "Colorear parcelas por",
        options=["cultivo", "rendimiento"],
        format_func=lambda x: "Cultivo clasificado" if x == "cultivo" else "Rendimiento (qq/ha)",
        horizontal=False,
        help="Elige la variable que define el color de cada polígono.",
    )

    # Indicador visual del ciclo activo
    color = COLORES_CICLO[ciclo]
    st.markdown(
        f"""
        <div style='margin-top:1rem; padding:.5rem .8rem;
                    border-left:4px solid {color}; background:#1a1d23;
                    border-radius:4px; font-size:.85rem;'>
            Ciclo activo: <b style='color:{color};'>{etiqueta_ciclo}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    return {"ciclo": ciclo, "ventana": ventana, "modo_color": modo_color}


def render_filtros_series() -> dict:
    """
    Filtros para la vista Series Temporales.

    Retorna
    -------
    dict con claves:
        ciclo    : str
        indices  : list[str] — índices a graficar ('EVI', 'LSWI', 'GPP')
    """
    _encabezado_sidebar()
    st.markdown("### 📈 Filtros — Series Temporales")

    etiqueta_ciclo = st.selectbox(
        "Ciclo de siembra",
        options=list(CICLOS.keys()),
    )
    ciclo = CICLOS[etiqueta_ciclo]

    st.markdown("---")
    st.markdown("**Índices a visualizar**")
    mostrar_evi  = st.checkbox("EVI",  value=True,  help="Enhanced Vegetation Index")
    mostrar_lswi = st.checkbox("LSWI", value=True,  help="Land Surface Water Index")
    mostrar_gpp  = st.checkbox("GPP",  value=True,  help="Gross Primary Production diario")

    indices = []
    if mostrar_evi:
        indices.append("EVI")
    if mostrar_lswi:
        indices.append("LSWI")
    if mostrar_gpp:
        indices.append("GPP")

    st.markdown("---")
    st.markdown("**Marcadores fenológicos**")
    mostrar_sos = st.checkbox("SOS — inicio de temporada", value=True)
    mostrar_pos = st.checkbox("POS — pico de temporada",   value=True)

    color = COLORES_CICLO[ciclo]
    st.markdown(
        f"""
        <div style='margin-top:1rem; padding:.5rem .8rem;
                    border-left:4px solid {color}; background:#1a1d23;
                    border-radius:4px; font-size:.85rem;'>
            Ciclo activo: <b style='color:{color};'>{etiqueta_ciclo}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    return {
        "ciclo":        ciclo,
        "indices":      indices,
        "mostrar_sos":  mostrar_sos,
        "mostrar_pos":  mostrar_pos,
    }


def render_filtros_estimacion() -> dict:
    """
    Filtros para la vista Estimación.

    Retorna
    -------
    dict con claves:
        ciclo   : str
        ventana : str
    """
    _encabezado_sidebar()
    st.markdown("### ⚖️ Filtros — Estimación")

    etiqueta_ciclo = st.selectbox(
        "Ciclo de siembra",
        options=list(CICLOS.keys()),
    )
    ciclo = CICLOS[etiqueta_ciclo]

    ventana = st.select_slider(
        "Ventana de predicción",
        options=VENTANAS,
        value="T1",
    )

    color = COLORES_CICLO[ciclo]
    st.markdown(
        f"""
        <div style='margin-top:1rem; padding:.5rem .8rem;
                    border-left:4px solid {color}; background:#1a1d23;
                    border-radius:4px; font-size:.85rem;'>
            Ciclo activo: <b style='color:{color};'>{etiqueta_ciclo}</b>
            &nbsp;·&nbsp; Ventana: <b>{ventana}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    return {"ciclo": ciclo, "ventana": ventana}


def render_filtros_resumen() -> dict:
    """
    Filtros para la vista Resumen Valle.

    Retorna
    -------
    dict con claves:
        ciclo   : str
        ventana : str
    """
    _encabezado_sidebar()
    st.markdown("### 📊 Filtros — Resumen Valle")

    etiqueta_ciclo = st.selectbox(
        "Ciclo de siembra",
        options=list(CICLOS.keys()),
    )
    ciclo = CICLOS[etiqueta_ciclo]

    ventana = st.select_slider(
        "Ventana de predicción",
        options=VENTANAS,
        value="T3",
        help="T3 proporciona la estimación más completa del ciclo.",
    )

    color = COLORES_CICLO[ciclo]
    st.markdown(
        f"""
        <div style='margin-top:1rem; padding:.5rem .8rem;
                    border-left:4px solid {color}; background:#1a1d23;
                    border-radius:4px; font-size:.85rem;'>
            Ciclo activo: <b style='color:{color};'>{etiqueta_ciclo}</b>
            &nbsp;·&nbsp; Ventana: <b>{ventana}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    return {"ciclo": ciclo, "ventana": ventana}
