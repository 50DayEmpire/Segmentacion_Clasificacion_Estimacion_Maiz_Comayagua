# components/mapa_parcelas.py — Mapa Folium de parcelas
"""
Renderiza el mapa interactivo de parcelas con st_folium().
Recibe los filtros ya resueltos; no hace queries a la base de datos.
Cuando no hay datos disponibles, muestra el mapa base con un aviso.
"""
import folium
import streamlit as st
from streamlit_folium import st_folium
from config import (
    MAPA_CENTRO_LAT,
    MAPA_CENTRO_LON,
    MAPA_ZOOM_INICIO,
    MAPA_TILES,
    COLORES_CULTIVO,
    RENDIMIENTO_MIN_QQ_HA,
    RENDIMIENTO_MAX_QQ_HA,
)


def _construir_mapa_base() -> folium.Map:
    """Crea el mapa Folium con la configuración base del observatorio."""
    mapa = folium.Map(
        location=[MAPA_CENTRO_LAT, MAPA_CENTRO_LON],
        zoom_start=MAPA_ZOOM_INICIO,
        tiles=MAPA_TILES,
        control_scale=True,
    )

    # Capa satelital opcional
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satélite (Esri)",
        overlay=False,
        control=True,
    ).add_to(mapa)

    folium.LayerControl(position="topright", collapsed=False).add_to(mapa)
    return mapa


def render_mapa_parcelas(filtros: dict) -> None:
    """
    Renderiza el mapa interactivo de parcelas.

    Parámetros
    ----------
    filtros : dict
        Diccionario con claves 'ciclo', 'ventana' y 'modo_color'
        generado por sidebar_filtros.render_filtros_parcelas().
    """
    ciclo      = filtros.get("ciclo", "primera")
    ventana    = filtros.get("ventana", "T1")
    modo_color = filtros.get("modo_color", "cultivo")

    mapa = _construir_mapa_base()

    # ── Marcador central de referencia ─────────────────────────────────────────
    folium.Marker(
        location=[MAPA_CENTRO_LAT, MAPA_CENTRO_LON],
        popup=folium.Popup(
            "<b>Valle de Comayagua</b><br>Área de estudio",
            max_width=200,
        ),
        tooltip="Valle de Comayagua",
        icon=folium.Icon(color="green", icon="leaf", prefix="fa"),
    ).add_to(mapa)

    # ── Contorno aproximado del área de estudio ────────────────────────────────
    folium.Circle(
        location=[MAPA_CENTRO_LAT, MAPA_CENTRO_LON],
        radius=12_000,          # ~12 km — placeholder hasta contar con polígono real
        color="#2ecc71",
        weight=1.5,
        fill=True,
        fill_color="#2ecc71",
        fill_opacity=0.04,
        tooltip="Área de estudio aproximada",
        dash_array="6 4",
    ).add_to(mapa)

    # ── Aviso de datos no disponibles ──────────────────────────────────────────
    st.warning(
        f"No hay parcelas cargadas para el ciclo **{ciclo}** / ventana **{ventana}**. "
        "El mapa muestra el área de estudio. Conecta la base de datos para visualizar polígonos.",
        icon="⚠️",
    )

    # ── Barra de información sobre el modo activo ──────────────────────────────
    modo_label = (
        "Cultivo clasificado" if modo_color == "cultivo"
        else f"Rendimiento estimado (qq/ha) — ventana {ventana}"
    )
    st.markdown(
        f"""
        <div style='background:#1a1d23; border:1px solid #2d3139; border-radius:6px;
                    padding:.5rem .9rem; margin-bottom:.5rem; font-size:.85rem;'>
            🎨 &nbsp;Modo de color: <b>{modo_label}</b>
            &nbsp;&nbsp;|&nbsp;&nbsp;
            📅 Ciclo: <b>{ciclo.title()}</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Renderizado con st_folium ──────────────────────────────────────────────
    st_folium(
        mapa,
        width="100%",
        height=560,
        returned_objects=["last_object_clicked"],
        key=f"mapa_parcelas_{ciclo}_{ventana}_{modo_color}",
    )
