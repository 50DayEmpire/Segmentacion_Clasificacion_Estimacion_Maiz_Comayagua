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


def _agregar_css_no_select(mapa: folium.Map) -> None:
    css = """
    <style>
        .leaflet-container, .leaflet-control-zoom, .leaflet-control-attribution,
        .leaflet-control-layers, .leaflet-popup-content-wrapper,
        .leaflet-control *, .leaflet-pane * {
            user-select: none !important;
            -webkit-user-select: none !important;
            -moz-user-select: none !important;
            -ms-user-select: none !important;
        }
    </style>"""
    mapa.get_root().header.add_child(folium.Element(css))


def _agregar_leyenda_flotante(mapa: folium.Map, modo_color: str) -> None:
    if modo_color == "cultivo":
        items = ""
        for cultivo, color in COLORES_CULTIVO.items():
            if cultivo not in ("maiz", "otro"):
                continue
            etiqueta = cultivo.replace("_", " ").title()
            items += f"""
            <div style="display:flex; align-items:center; gap:.6rem; padding:.2rem 0;">
                <div style="width:14px;height:14px;border-radius:3px;background:{color};flex-shrink:0;"></div>
                <span>{etiqueta}</span>
            </div>"""
        html = f"""
        <div style="position:absolute;bottom:25px;right:20px;z-index:1000;
                    background:rgba(26,29,35,.92);border:1px solid #2d3139;border-radius:8px;
                    padding:.7rem 1rem;font-size:.85rem;color:#eee;min-width:120px;
                    box-shadow:0 4px 15px rgba(0,0,0,.5);backdrop-filter:blur(4px);
                    font-family:system-ui,-apple-system,sans-serif;">
            <div style="font-weight:600;margin-bottom:.4rem;font-size:.9rem;border-bottom:1px solid #2d3139;padding-bottom:.3rem;">Leyenda</div>
            {items}
        </div>"""
    else:
        html = f"""
        <div style="position:absolute;bottom:25px;right:20px;z-index:1000;
                    background:rgba(26,29,35,.92);border:1px solid #2d3139;border-radius:8px;
                    padding:.7rem 1rem;font-size:.85rem;color:#eee;min-width:140px;
                    box-shadow:0 4px 15px rgba(0,0,0,.5);backdrop-filter:blur(4px);
                    font-family:system-ui,-apple-system,sans-serif;">
            <div style="font-weight:600;margin-bottom:.4rem;font-size:.9rem;border-bottom:1px solid #2d3139;padding-bottom:.3rem;">Rendimiento (qq/ha)</div>
            <div style="display:flex;justify-content:space-between;font-size:.8rem;color:#95a5a6;margin-bottom:.15rem;">
                <span>0</span><span>120</span>
            </div>
            <div style="height:12px;border-radius:4px;background:linear-gradient(to right,#2c3e50,#2ecc71);"></div>
        </div>"""
    mapa.get_root().html.add_child(folium.Element(html))


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

    # ── Estado del mapa (centering → idle) ────────────────────────────────────
    if "centrar_revision" not in st.session_state:
        st.session_state["centrar_revision"] = 0
    if "mapa_centrar_pendiente" not in st.session_state:
        st.session_state["mapa_centrar_pendiente"] = False

    # ── Botón centrar área de estudio ──────────────────────────────────────────
    if st.button(
        "🎯 Centrar en área de estudio",
        help="Ajusta el mapa para mostrar el Valle de Comayagua completo.",
        key="btn_centrar_mapa",
        use_container_width=False,
    ):
        st.session_state["mapa_centrar_pendiente"] = True
        st.session_state["centrar_revision"] += 1

    # ── Cargar datos ───────────────────────────────────────────────────────────
    gdf_municipio = cargar_municipio()
    gdf_parcelas  = cargar_parcelas(layer=LAYERS_GPKG.get("parcelas", "parcelas_vigentes"))

    # ── Construir mapa base ────────────────────────────────────────────────────
    mapa = _construir_mapa_base(centrar_en_estudio=st.session_state["mapa_centrar_pendiente"])

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

    # ── fit_bounds (solo cuando se pulsa "Centrar") ────────────────────────────
    if st.session_state["mapa_centrar_pendiente"] and not gdf_municipio.empty:
        b = MUNICIPIO_BOUNDS
        mapa.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
        st.session_state["mapa_centrar_pendiente"] = False

    # ── Leyenda flotante y CSS ──────────────────────────────────────────────────
    _agregar_css_no_select(mapa)
    _agregar_leyenda_flotante(mapa, modo_color)

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
