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


def render_filtros_historico() -> dict:
    """
    Filtros para la vista Análisis Histórico.

    Retorna
    -------
    dict con claves:
        anio        : int  — año seleccionado
        ciclo       : str  — 'primera' | 'postrera'
        id_ciclo    : int | None — ciclo específico seleccionado
        ventana     : str  — 'T1' | 'T2' | 'T3'
    """
    _encabezado_sidebar()
    st.markdown("### 🗂️ Filtros — Histórico")

    from config import CICLOS, VENTANAS, ANIOS_HISTORICO, COLORES_CICLO

    etiqueta_ciclo = st.selectbox(
        "Ciclo de siembra",
        options=list(CICLOS.keys()),
        help="Filtra por temporada de cultivo.",
    )
    ciclo = CICLOS[etiqueta_ciclo]

    anio = st.select_slider(
        "Año histórico",
        options=ANIOS_HISTORICO,
        value=ANIOS_HISTORICO[-1],
        key="timeline_anio",
    )

    st.markdown("---")

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
            {etiqueta_ciclo} · {anio}
            &nbsp;·&nbsp; Ventana: <b>{ventana}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    return {"anio": anio, "ciclo": ciclo, "ventana": ventana}


def render_filtros_segmentacion() -> dict:
    """
    Filtros para la vista Segmentación de Parcelas.

    Escanea ``CAPAS_SEGMENTACION`` en busca de archivos ``.gpkg``
    generados por delineate-anything (borradores) y presenta GPKGs
    validados (``data/*.gpkg`` con seeding) para seleccionar BD activa.

    Retorna
    -------
    dict con claves:
        archivo_gpkg : str  — ruta completa al archivo .gpkg seleccionado (borrador)
        nombre_capa  : str  — nombre del archivo (ej. "Sample.gpkg")
        es_borrador  : bool — True si el seleccionado está en CAPAS_SEGMENTACION
    """
    from pathlib import Path
    from config import CAPAS_SEGMENTACION, ROOT
    from utils.conexionDB import get_db_path, listar_gpkgs_validados, set_db_path
    from utils.db import validar_gpkg
    from utils.gpkg_layer import asegurar_capa_parcelas, limpiar_legacy

    _encabezado_sidebar()

    archivo_gpkg = None
    nombre_capa = None
    es_borrador = False

    # ── Sección: Borradores (delineate_anything) ──────────────────────────
    st.markdown("### 📁 Borradores")
    ruta_drafts = Path(CAPAS_SEGMENTACION)
    gpkg_drafts = sorted(ruta_drafts.glob("*.simp.gpkg")) if ruta_drafts.is_dir() else []

    if not gpkg_drafts:
        st.info("No hay borradores .gpkg en el directorio de segmentación.", icon="📭")
    else:
        opciones = {}
        for f in gpkg_drafts:
            tam = f.stat().st_size
            if tam < 1024:
                tam_str = f"{tam} B"
            elif tam < 1024 ** 2:
                tam_str = f"{tam / 1024:.1f} KB"
            else:
                tam_str = f"{tam / 1024 ** 2:.1f} MB"
            label = f"{f.name}  ({tam_str})"
            opciones[label] = str(f)

        draft_key = st.radio(
            "Selecciona un borrador",
            options=list(opciones.keys()),
            index=0,
            key="draft_selector",
            help="Capa generada por el modelo de segmentación (delineate-anything).",
        )

        archivo_gpkg = opciones[draft_key]
        nombre_capa = Path(archivo_gpkg).name
        es_borrador = True
        asegurar_capa_parcelas(archivo_gpkg)
        limpiar_legacy(archivo_gpkg)

        if st.button("✅ Validar a producción", use_container_width=True,
                      help="Copia a data/ y ejecuta seeding (crea tablas del pipeline)."):
            with st.spinner("Validando GPKG…"):
                try:
                    destino = validar_gpkg(archivo_gpkg)
                    st.success(f"Validado: {destino.name}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al validar: {e}", icon="❌")

    st.divider()

    # ── Sección: Bases de datos validadas ─────────────────────────────────
    st.markdown("### 🗄️ BD Validadas")
    gpkgs_validados = listar_gpkgs_validados()

    if not gpkgs_validados:
        st.info("No hay GPKGs validados en data/.", icon="📭")
    else:
        db_actual = get_db_path().resolve()
        opciones_bd = {p.name: str(p) for p in gpkgs_validados}
        idx_actual = 0
        for i, p in enumerate(gpkgs_validados):
            if p.resolve() == db_actual:
                idx_actual = i
                break

        bd_seleccionada = st.selectbox(
            "Base de datos activa",
            options=list(opciones_bd.keys()),
            index=idx_actual,
            key="bd_selector",
            help="Cambia la BD activa del observatorio (solo GPKGs con seeding).",
        )

        bd_path = opciones_bd[bd_seleccionada]
        if Path(bd_path).resolve() != db_actual:
            set_db_path(bd_path)
            st.cache_data.clear()
            st.rerun()

        st.markdown(
            f"""<div style='margin-top:.5rem; padding:.5rem .8rem;
                        border-left:4px solid #2ecc71; background:#1a1d23;
                        border-radius:4px; font-size:.85rem;'>
                🟢 BD activa: <b style='color:#2ecc71;'>{bd_seleccionada}</b>
            </div>""",
            unsafe_allow_html=True,
        )

    return {
        "archivo_gpkg": archivo_gpkg,
        "nombre_capa": nombre_capa,
        "es_borrador": es_borrador,
    }
