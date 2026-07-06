# components/mapa_parcelas.py — Mapa Folium de parcelas
"""
Renderiza el mapa interactivo de parcelas con st_folium().
- Capa 1: contorno del municipio de Comayagua (área de estudio).
- Capa 2: parcelas segmentadas, coloreadas por cultivo o rendimiento.
- Botón para centrar el mapa en el área de estudio.
"""
import folium
import streamlit as st
from streamlit_folium import st_folium
from folium.plugins import Fullscreen

from config import (
    MAPA_CENTRO_LAT,
    MAPA_CENTRO_LON,
    MAPA_ZOOM_INICIO,
    MAPA_TILES,
    COLORES_CULTIVO,
    COLORES_CICLO,
    LAYERS_GPKG,
    MUNICIPIO_BOUNDS,
)
from utils.queries import cargar_parcelas, cargar_municipio
from utils.capas_folium import agregar_capa_poligonos


def _construir_mapa_base(centrar_en_estudio: bool) -> folium.Map:
    """
    Crea el mapa Folium base.
    Si centrar_en_estudio=True hace fit_bounds al municipio;
    si no, usa el centro y zoom por defecto.
    """
    if centrar_en_estudio:
        # Inicializar en centro del municipio; fit_bounds se aplica después
        mapa = folium.Map(
            location=[MAPA_CENTRO_LAT, MAPA_CENTRO_LON],
            zoom_start=11,
            tiles=MAPA_TILES,
            control_scale=True,
        )
    else:
        mapa = folium.Map(
            location=[MAPA_CENTRO_LAT, MAPA_CENTRO_LON],
            zoom_start=MAPA_ZOOM_INICIO,
            tiles=MAPA_TILES,
            control_scale=True,
        )

    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Esri",
        name="Satélite (Esri)",
        overlay=False,
        control=True,
    ).add_to(mapa)

    return mapa


def render_mapa_parcelas(filtros: dict) -> dict | None:
    """
    Renderiza el mapa interactivo de parcelas.

    Parámetros
    ----------
    filtros : dict
        Claves: 'ciclo', 'ventana', 'modo_color'.

    Retorna
    -------
    dict | None
        Resultado de ``st_folium`` con ``last_object_clicked``.
    """
    ciclo      = filtros.get("ciclo", "primera")
    ventana    = filtros.get("ventana", "T1")
    modo_color = filtros.get("modo_color", "cultivo")

    # ── Estado del mapa (initial → idle / centering → idle) ──────────────────
    if "mapa_estado" not in st.session_state:
        st.session_state["mapa_estado"] = "initial"
    if "centrar_revision" not in st.session_state:
        st.session_state["centrar_revision"] = 0

    # ── Botón centrar área de estudio ──────────────────────────────────────────
    if st.button(
        "🎯 Centrar en área de estudio",
        help="Ajusta el mapa para mostrar el Valle de Comayagua completo.",
        use_container_width=False,
    ):
        st.session_state["mapa_estado"] = "centering"
        st.session_state["centrar_revision"] += 1

    # ── Cargar datos ───────────────────────────────────────────────────────────
    gdf_municipio = cargar_municipio()
    gdf_parcelas  = cargar_parcelas(layer=LAYERS_GPKG.get("parcelas", "parcelas_vigentes"))

    # ── Construir mapa base ────────────────────────────────────────────────────
    mapa = _construir_mapa_base(centrar_en_estudio=st.session_state["mapa_estado"] == "centering")

    # ── Capa 1: contorno del municipio ─────────────────────────────────────────
    if not gdf_municipio.empty:
        mapa = agregar_capa_poligonos(
            mapa=mapa,
            gdf=gdf_municipio,
            nombre_capa="Municipio de Comayagua",
            color_borde="#3498db",
            color_relleno="#3498db",
            opacidad_relleno=0.05,
            opacidad_borde=0.9,
            peso_borde=2.5,
            columnas_popup=["NOMBRE", "superf_ha", "Area_Km2"],
            mostrar_tooltip=False,
            resaltar_hover=False,
        )

    # ── Capa 2: parcelas segmentadas ───────────────────────────────────────────
    columnas_reales = [c for c in ["id_parcela", "area_ha", "area_m2"] if c in gdf_parcelas.columns]

    if gdf_parcelas.empty:
        st.warning(
            "No hay parcelas en la base de datos. "
            "Ejecuta el seeding (`python main.py`) para cargar geometrías.",
            icon="⚠️",
        )
    else:
        color = COLORES_CICLO.get(ciclo, "#2ecc71")

        if modo_color == "cultivo" and "cultivo" in gdf_parcelas.columns:
            mapa = agregar_capa_poligonos(
                mapa=mapa,
                gdf=gdf_parcelas,
                nombre_capa="Parcelas — cultivo",
                columna_color="cultivo",
                mapa_colores=COLORES_CULTIVO,
                columnas_popup=columnas_reales + ["cultivo"],
            )
        else:
            mapa = agregar_capa_poligonos(
                mapa=mapa,
                gdf=gdf_parcelas,
                nombre_capa="Parcelas — área",
                color_borde=color,
                color_relleno=color,
                columnas_popup=columnas_reales,
            )

    # ── fit_bounds ─────────────────────────────────────────────────────────────
    estado = st.session_state["mapa_estado"]
    if estado == "centering" and not gdf_municipio.empty:
        b = MUNICIPIO_BOUNDS
        mapa.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
        st.session_state["mapa_estado"] = "idle"
    elif estado == "initial" and not gdf_parcelas.empty:
        b = gdf_parcelas.total_bounds
        mapa.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
        st.session_state["mapa_estado"] = "idle"

    # ── LayerControl ───────────────────────────────────────────────────────────
    folium.LayerControl(position="topright", collapsed=False).add_to(mapa)

    # ── Botón de pantalla completa ───────────────────────────────────────────────
    Fullscreen(position="topleft", title="Pantalla completa", title_cancel="Salir de pantalla completa").add_to(mapa)

    # ── Barra de estado ────────────────────────────────────────────────────────
    modo_label = (
        "Cultivo clasificado" if modo_color == "cultivo"
        else f"Rendimiento estimado (qq/ha) — ventana {ventana}"
    )
    n_parcelas = len(gdf_parcelas) if not gdf_parcelas.empty else 0
    st.markdown(
        f"""
        <div style='background:#1a1d23; border:1px solid #2d3139; border-radius:6px;
                    padding:.5rem .9rem; margin-bottom:.5rem; font-size:.85rem;
                    display:flex; gap:1.5rem; flex-wrap:wrap;'>
            <span>🎨 <b>{modo_label}</b></span>
            <span>📅 Ciclo: <b>{ciclo.title()}</b></span>
            <span>🗺️ Parcelas: <b>{n_parcelas}</b></span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Renderizado con st_folium ──────────────────────────────────────────────
    return st_folium(
        mapa,
        width="100%",
        height=560,
        returned_objects=["last_object_clicked"],
        key=f"mapa_parcelas_{ciclo}_{ventana}_{modo_color}_v{st.session_state['centrar_revision']}",
    )
