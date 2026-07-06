# utils/capas_folium.py — Función reutilizable para agregar polígonos a Folium
"""
Función pura: recibe un GeoDataFrame ya en EPSG:4326 y un mapa Folium,
agrega los polígonos como una sola capa GeoJson y retorna el mapa.
Sin llamadas a st.* ni a la base de datos.
"""
from __future__ import annotations

import folium
import geopandas as gpd
from shapely import force_2d


def agregar_capa_poligonos(
    mapa: folium.Map,
    gdf: gpd.GeoDataFrame,
    nombre_capa: str = "Parcelas",
    color_borde: str = "#2ecc71",
    color_relleno: str = "#2ecc71",
    opacidad_relleno: float = 0.35,
    opacidad_borde: float = 0.9,
    peso_borde: float = 1.5,
    columna_color: str | None = None,
    mapa_colores: dict[str, str] | None = None,
    columnas_popup: list[str] | None = None,
    mostrar_tooltip: bool = True,
) -> folium.Map:
    """
    Agrega una capa de polígonos desde un GeoDataFrame a un mapa Folium.

    El GeoDataFrame debe estar en EPSG:4326. Para colorear por atributo
    pasa `columna_color` y `mapa_colores` (dict valor→color hex).

    Parámetros
    ----------
    mapa : folium.Map
    gdf : gpd.GeoDataFrame     — polígonos en EPSG:4326
    nombre_capa : str          — etiqueta en el LayerControl
    color_borde : str          — color hex de borde (sin columna_color)
    color_relleno : str        — color hex de relleno (sin columna_color)
    opacidad_relleno : float
    opacidad_borde : float
    peso_borde : float
    columna_color : str | None — columna para colorear por categoría
    mapa_colores : dict | None — {valor: "#hex"}
    columnas_popup : list | None — columnas del popup; None = todas menos geometry

    Retorna
    -------
    folium.Map con la capa agregada.
    """
    if gdf.empty:
        return mapa

    gdf = gdf.copy()

    # Eliminar coordenada Z — puede causar que Leaflet no renderice polígonos
    gdf["geometry"] = gdf["geometry"].apply(force_2d)

    # ── Columna interna de color ────────────────────────────────────────────────
    # Debe definirse ANTES de calcular cols_existentes para excluirla del popup.
    _COLOR_COL = "_color_relleno"
    if columna_color and mapa_colores and columna_color in gdf.columns:
        gdf[_COLOR_COL] = gdf[columna_color].apply(
            lambda v: mapa_colores.get(str(v).lower(), color_relleno)
        )
    else:
        gdf[_COLOR_COL] = color_relleno

    # ── Columnas para tooltip y popup ──────────────────────────────────────────
    # Filtrar contra las columnas reales del GDF para evitar AssertionError
    # de GeoJsonTooltip/GeoJsonPopup cuando alguna columna no existe.
    geom_col = gdf.geometry.name
    candidatas = columnas_popup or [
        c for c in gdf.columns if c not in (geom_col, _COLOR_COL)
    ]
    cols_existentes = [
        c for c in candidatas
        if c in gdf.columns and c not in (geom_col, _COLOR_COL)
    ]

    # ── style_function ─────────────────────────────────────────────────────────
    def _style(feature):
        c = feature["properties"].get(_COLOR_COL, color_relleno)
        return {
            "fillColor":   c,
            "color":       c,
            "weight":      peso_borde,
            "fillOpacity": opacidad_relleno,
            "opacity":     opacidad_borde,
        }

    # ── Tooltip y popup via onEachFeature ─────────────────────────────────────
    # Usamos on_each_feather para que streamlit-folium pueda capturar
    # last_object_clicked con propiedades (no solo lat/lng).
    tooltip  = None
    on_each  = None

    if cols_existentes and mostrar_tooltip:
        tooltip = folium.GeoJsonTooltip(
            fields=cols_existentes[:3],
            aliases=[f"{c}:" for c in cols_existentes[:3]],
            localize=True,
        )
        html_parts = "".join(
            f" + '<b>{c}:</b> ' + feature.properties['{c}'] + '<br>'"
            for c in cols_existentes
        )
        # Importante: streamlit-folium siempre setea last_object_clicked = t.latlng
        # en su onLayerClick. Usamos Promise.resolve().then() como microtask para
        # sobrescribir con feature.properties DESPUÉS de onLayerClick pero
        # ANTES de updateComponentValue (250ms debounce).
        on_each = folium.JsCode(f"""
        function(feature, layer) {{
            layer.bindPopup('<div style="min-width:120px;">' {html_parts} + '</div>');
            layer.on('click', function(e) {{
                Promise.resolve().then(function() {{
                    window.__GLOBAL_DATA__.last_object_clicked = feature.properties;
                }});
            }});
        }}
        """)

    # ── Añadir capa al mapa ────────────────────────────────────────────────────
    grupo = folium.FeatureGroup(name=nombre_capa, show=True)

    folium.GeoJson(
        gdf,
        style_function=_style,
        on_each_feature=on_each,
        tooltip=tooltip,
        name=nombre_capa,
    ).add_to(grupo)

    grupo.add_to(mapa)
    return mapa
