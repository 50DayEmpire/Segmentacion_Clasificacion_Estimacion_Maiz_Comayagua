# pages/8_Segmentacion_Parcelas.py — Administración: segmentación de parcelas
from pathlib import Path

import geopandas as gpd
import streamlit as st
from components.mapa_segmentacion import render_mapa_segmentacion, limpiar_cache as limpiar_cache_html
from components.sidebar_filtros import render_filtros_segmentacion


@st.cache_data(show_spinner=False)
def _cargar_gpkg_cache(ruta: str) -> gpd.GeoDataFrame:
    return gpd.read_file(ruta)


st.markdown("## ✂️ Segmentación de Parcelas")
st.markdown(
    "Visualiza y edita las capas de polígonos generadas por el modelo de segmentación "
    "([delineate-anything](https://github.com/anomalyco/delineate-anything)). "
    "Selecciona una capa en el panel izquierdo. Los polígonos se pueden editar "
    "con las herramientas del mapa."
)
st.divider()

with st.sidebar:
    capa_info = render_filtros_segmentacion()
    st.divider()
    if st.button("🔄 Limpiar caché", use_container_width=True, help="Recarga la capa desde el archivo"):
        _cargar_gpkg_cache.clear()
        limpiar_cache_html()
        st.rerun()

gdf_capa = None
nombre_capa = None
if capa_info["archivo_gpkg"] and Path(capa_info["archivo_gpkg"]).exists():
    try:
        gdf_capa = _cargar_gpkg_cache(capa_info["archivo_gpkg"])
        nombre_capa = capa_info["nombre_capa"]
    except Exception as e:
        st.error(f"Error al leer la capa: {e}", icon="❌")

resultado = render_mapa_segmentacion(
    gdf=gdf_capa,
    nombre_capa=nombre_capa,
    archivo_gpkg=capa_info.get("archivo_gpkg"),
)

# ── Guardado de ediciones ─────────────────────────────────────────────────
if gdf_capa is not None and not gdf_capa.empty:
    st.divider()
    st.caption(
        "Usa los botones del mapa (esquina superior izquierda): "
        "✏️ editar vértices, ⬡ dibujar polígono, ▬ dibujar rectángulo, "
        "🗑️ eliminar seleccionado, 💾 descargar GeoJSON. "
        "Clic en un polígono para seleccionarlo; tecla **Supr** para eliminarlo."
    )
