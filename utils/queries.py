# utils/queries.py — Consultas a la base de datos con caché de Streamlit
"""
Toda función que lea geometrías o tablas del .gpkg debe vivir aquí.
Reglas:
- @st.cache_data en todas las funciones (nunca corren sin caché en Streamlit).
- Sin llamadas a st.* salvo el decorador de caché.
- Geometrías se retornan en EPSG:4326 listas para Folium.
"""
import traceback
import pandas as pd
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


@st.cache_data(show_spinner="Cargando lista de parcelas…")
def cargar_lista_parcelas() -> list[int]:
    """
    Consulta los ``id_parcela`` disponibles en ``parcelas_vigentes``.

    Retorna
    -------
    list[int]
        Lista ordenada de identificadores de parcela, o lista vacía si
        la capa no existe o la consulta falla.
    """
    try:
        gdf = gpd.read_file(str(GPKG_PATH), layer="parcelas_vigentes")
        return sorted(gdf["id_parcela"].dropna().unique().tolist())
    except Exception:
        traceback.print_exc()
        return []


@st.cache_data(show_spinner="Cargando series temporales…")
def cargar_datos_series(parcela_id: int) -> dict | None:
    """
    Carga índices crudos y suavizados (Whittaker) para una parcela.

    Retorna
    -------
    dict | None
        ``{"raw": {"EVI": Series, "LSWI": Series},
            "smoothed": {"EVI": Series, "LSWI": Series}}``
        o ``None`` si no hay datos.
    """
    try:
        from pipeline.ingesta import cargar_indices_desde_bd
        from pipeline.modulo_vpm import preprocesar_indices_vpm

        dfs_crudos = cargar_indices_desde_bd(ids_parcelas=[parcela_id])
        col = f"id_{parcela_id}"

        dfs_suave = preprocesar_indices_vpm(dfs_crudos)

        raw_evi = dfs_crudos["EVI"][col].dropna()
        raw_lswi = dfs_crudos["LSWI"][col].dropna()

        # Recortar bordes aislados (gap > 60 días) para que el eje X
        # no se expanda artificialmente con outlieres puntuales.
        def _recortar_bordes(s: pd.Series) -> pd.Series:
            if len(s) < 3:
                return s
            gaps = s.index.to_series().diff()
            if gaps.iloc[1] > pd.Timedelta(days=60):
                s = s.iloc[1:]
            gaps = s.index.to_series().diff()
            if len(s) > 1 and gaps.iloc[-1] > pd.Timedelta(days=60):
                s = s.iloc[:-1]
            return s

        raw_evi = _recortar_bordes(raw_evi)
        raw_lswi = _recortar_bordes(raw_lswi)

        # Alinear suavizado al mismo rango temporal del crudo
        lo = min(raw_evi.index.min(), raw_lswi.index.min())
        hi = max(raw_evi.index.max(), raw_lswi.index.max())
        smooth_evi = dfs_suave["EVI"][col].loc[lo:hi]
        smooth_lswi = dfs_suave["LSWI"][col].loc[lo:hi]

        return {
            "raw": {"EVI": raw_evi, "LSWI": raw_lswi},
            "smoothed": {"EVI": smooth_evi, "LSWI": smooth_lswi},
        }
    except ValueError:
        return None
    except Exception:
        traceback.print_exc()
        return None


@st.cache_data(show_spinner="Cargando ciclos históricos…")
def cargar_ciclos_historicos(
    anio: int | None = None,
    temporada: str | None = None,
    id_parcela: int | None = None,
) -> pd.DataFrame:
    """
    Consulta ``produccion_acumulada_ciclo`` con filtros opcionales
    de año de SOS, temporada y parcela.

    Parámetros
    ----------
    anio : int | None
        Año del SOS (se extrae con ``strftime('%Y', sos)``).
    temporada : str | None
        ``'primera'`` o ``'postrera'``.
    id_parcela : int | None
        Filtra por parcela específica.

    Retorna
    -------
    pd.DataFrame
        Columnas: id_ciclo, id_parcela, temporada, sos, t1, t2, t3, eos,
        rendimiento, produccion_total, lswi_max, estado_ciclo.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    condiciones: list[str] = []
    params: list = []

    if anio is not None:
        condiciones.append("CAST(strftime('%Y', sos) AS INTEGER) = ?")
        params.append(anio)
    if temporada is not None:
        condiciones.append("temporada = ?")
        params.append(temporada)
    if id_parcela is not None:
        condiciones.append("id_parcela = ?")
        params.append(id_parcela)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    sql = f"""
        SELECT id_ciclo, id_parcela, temporada, sos,
               t1, t2, t3, eos,
               rendimiento, produccion_total, lswi_max, estado_ciclo
        FROM produccion_acumulada_ciclo
        {where}
        ORDER BY sos, id_parcela
    """
    with closing(get_connection_raw()) as conn:
        return pd.read_sql(sql, conn, params=params, parse_dates=["sos", "t1", "t2", "t3", "eos"])


@st.cache_data(show_spinner="Cargando predicciones…")
def cargar_predicciones_ciclo(id_ciclo: int) -> pd.DataFrame:
    """
    Consulta ``predicciones_ventana`` para un ciclo dado.

    Retorna
    -------
    pd.DataFrame
        Columnas: ventana, fecha_ventana, gpp_acumulado, npp_acumulado,
        rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    sql = """
        SELECT ventana, fecha_ventana,
               gpp_acumulado, npp_acumulado,
               rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela
        FROM predicciones_ventana
        WHERE id_ciclo = ?
        ORDER BY ventana
    """
    with closing(get_connection_raw()) as conn:
        return pd.read_sql(sql, conn, params=(id_ciclo,), parse_dates=["fecha_ventana"])


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
