# utils/queries.py — Consultas a la base de datos con caché de Streamlit
"""
Toda función que lea geometrías o tablas del .gpkg debe vivir aquí.
Reglas:
- @st.cache_data en todas las funciones (nunca corren sin caché en Streamlit).
- Sin llamadas a st.* salvo el decorador de caché.
- Geometrías se retornan en EPSG:4326 listas para Folium.
"""
import traceback
import geopandas as gpd
import streamlit as st
from config import GPKG_PATH, CRS_GEOGRAFICO, CRS_METRICO


@st.cache_data(show_spinner="Cargando parcelas…")
def cargar_parcelas(layer: str = "parcelas_vigentes") -> gpd.GeoDataFrame:
    """
    Lee la capa de parcelas del GeoPackage y la retorna en EPSG:4326.

    Retorna GeoDataFrame vacío (con esquema mínimo) si la capa no existe.
    Imprime el traceback completo en consola para no perder errores.
    """
    try:
        gdf = gpd.read_file(str(GPKG_PATH), layer=layer)
        epsg_metrico = int(CRS_METRICO.split(":")[1])
        if gdf.crs is None or gdf.crs.to_epsg() != epsg_metrico:
            gdf = gdf.set_crs(CRS_METRICO, allow_override=True)
        return gdf.to_crs(CRS_GEOGRAFICO)
    except Exception:
        traceback.print_exc()   # visible en la terminal de Streamlit
        return gpd.GeoDataFrame(
            columns=["id_parcela", "area_ha", "area_m2", "geometry"],
            geometry="geometry",
            crs=CRS_GEOGRAFICO,
        )


@st.cache_data(show_spinner="Cargando área de estudio…")
def cargar_municipio() -> gpd.GeoDataFrame:
    """
    Lee el polígono del municipio de Comayagua desde el archivo GeoJSON estático
    y lo retorna en EPSG:4326.
    """
    try:
        from config import MUNICIPIO_GEOJSON
        gdf = gpd.read_file(str(MUNICIPIO_GEOJSON))
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:32616", allow_override=True)
        return gdf.to_crs("EPSG:4326")
    except Exception:
        traceback.print_exc()
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
