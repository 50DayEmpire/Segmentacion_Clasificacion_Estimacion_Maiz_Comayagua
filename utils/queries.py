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
from utils.conexionDB import get_db_path
from config import GPKG_PATH, CRS_GEOGRAFICO, CRS_METRICO


@st.cache_data(show_spinner="Cargando parcelas…")
def cargar_parcelas(layer: str = "parcelas_vigentes") -> gpd.GeoDataFrame:
    """
    Lee la capa de parcelas del GeoPackage y la retorna en EPSG:4326.

    Retorna GeoDataFrame vacío (con esquema mínimo) si la capa no existe.
    Imprime el traceback completo en consola para no perder errores.
    """
    try:
        _gpkg = str(get_db_path())
        gdf = gpd.read_file(_gpkg, layer=layer)
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
        _gpkg = str(get_db_path())
        gdf = gpd.read_file(_gpkg, layer="parcelas_vigentes")
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
        rendimiento, produccion_total, lswi_max, estado_ciclo,
        fecha_inicio, fecha_fin.
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
               rendimiento, produccion_total, lswi_max, estado_ciclo,
               clasificacion_final, fecha_inicio, fecha_fin
        FROM produccion_acumulada_ciclo
        {where}
        ORDER BY sos, id_parcela
    """
    with closing(get_connection_raw()) as conn:
        return pd.read_sql(sql, conn, params=params,
                           parse_dates=["sos", "t1", "t2", "t3", "eos", "fecha_inicio", "fecha_fin"])


@st.cache_data(show_spinner="Cargando predicciones…")
def cargar_predicciones_ciclo(id_ciclo: int) -> pd.DataFrame:
    """
    Consulta ``predicciones_ventana`` para un ciclo dado.

    Retorna
    -------
    pd.DataFrame
        Columnas: ventana, fecha_ventana, gpp_acumulado, npp_acumulado,
        rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela,
        score_compuesto, score_pearson, score_magnitud_pendiente,
        cultivo_predicho.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    sql = """
        SELECT id_prediccion, ventana, fecha_ventana,
               gpp_acumulado, npp_acumulado,
               rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela,
               score_compuesto, score_pearson, score_magnitud_pendiente,
               cultivo_predicho
        FROM predicciones_ventana
        WHERE id_ciclo = ?
        ORDER BY ventana
    """
    with closing(get_connection_raw()) as conn:
        return pd.read_sql(sql, conn, params=(id_ciclo,), parse_dates=["fecha_ventana"])


@st.cache_data(show_spinner="Cargando índices suavizados…")
def cargar_indices_suavizados(id_ciclo: int) -> pd.DataFrame | None:
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    sql = """
        SELECT fecha, evi, lswi
        FROM indices_suavizados
        WHERE id_ciclo = ?
        ORDER BY fecha
    """
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(id_ciclo,), parse_dates=["fecha"])
    return df if not df.empty else None


@st.cache_data(show_spinner="Cargando índices crudos…")
def cargar_indices_crudos(id_parcela: int, fecha_inicio, fecha_fin) -> pd.DataFrame | None:
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    sql = """
        SELECT fecha, evi_crudo AS evi, lswi_crudo AS lswi
        FROM series_diarias_vpm
        WHERE id_parcela = ? AND fecha >= ? AND fecha <= ?
        ORDER BY fecha
    """
    fecha_ini = fecha_inicio.strftime("%Y-%m-%d") if hasattr(fecha_inicio, "strftime") else str(fecha_inicio)
    fecha_fnl = fecha_fin.strftime("%Y-%m-%d") if hasattr(fecha_fin, "strftime") else str(fecha_fin)
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(id_parcela, fecha_ini, fecha_fnl), parse_dates=["fecha"])
    return df if not df.empty else None


@st.cache_data(show_spinner="Cargando extrapolación…")
def cargar_extrapolacion_prediccion(id_prediccion: int) -> dict | None:
    """
    Carga la serie extrapolada (cola proyectada) de una predicción.

    Retorna
    -------
    dict | None
        ``{"EVI": pd.Series, "LSWI": pd.Series}`` con DatetimeIndex,
        o ``None`` si no hay datos.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    sql = """
        SELECT fecha, evi_extrapolado, lswi_extrapolado
        FROM series_extrapoladas_ventana
        WHERE id_prediccion = ?
        ORDER BY fecha
    """
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(id_prediccion,), parse_dates=["fecha"])
    if df.empty:
        return None
    evi = df.set_index("fecha")["evi_extrapolado"].dropna()
    lswi = df.set_index("fecha")["lswi_extrapolado"].dropna()
    if evi.empty and lswi.empty:
        return None
    result = {}
    if not evi.empty:
        result["EVI"] = evi
    if not lswi.empty:
        result["LSWI"] = lswi
    return result


@st.cache_data(show_spinner="Cargando ciclos…")
def cargar_ciclos_no_finalizados(temporada: str, anio: int | None = None) -> pd.DataFrame:
    """
    Consulta ciclos para la temporada indicada, trayendo el score compuesto
    y cultivo predicho de la ventana de predicción más avanzada disponible.

    Parameters
    ----------
    temporada : str
        ``"primera"`` o ``"postrera"``.
    anio : int | None
        Si se especifica, retorna **todos** los ciclos de ese año
        (sin filtrar por estado). Si es ``None``, retorta solo los
        ciclos ``candidato`` o ``activo`` (comportamiento histórico).

    Retorna
    -------
    pd.DataFrame
        Columnas: id_ciclo, id_parcela, sos, t1, t2, t3, eos,
        estado_ciclo, temporada,
        score_compuesto, cultivo_predicho, score_pearson,
        score_magnitud_pendiente, rendimiento_estimado_qq_ha,
        clasificacion_final.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    with closing(get_connection_raw()) as conn:
        col_exists = "clasificacion_final" in [
            r[1] for r in conn.execute(
                "PRAGMA table_info(produccion_acumulada_ciclo)"
            ).fetchall()
        ]

    clasif_col = "pac.clasificacion_final" if col_exists else "NULL AS clasificacion_final"

    params: list = []
    where_clauses: list[str] = []

    if anio:
        where_clauses.append("CAST(strftime('%Y', pac.sos) AS INTEGER) = ?")
        params.append(anio)
    else:
        where_clauses.append("pac.estado_ciclo IN ('candidato', 'activo')")

    where_clauses.append("pac.temporada = ?")
    params.append(temporada)

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        WITH ultima_ventana AS (
            SELECT id_ciclo, ventana, score_compuesto, cultivo_predicho,
                   score_pearson, score_magnitud_pendiente,
                   rendimiento_estimado_qq_ha,
                   ROW_NUMBER() OVER (
                       PARTITION BY id_ciclo
                       ORDER BY
                           CASE WHEN score_compuesto IS NOT NULL THEN 0 ELSE 1 END,
                           CASE ventana
                               WHEN 'EOS' THEN 4 WHEN 'T3' THEN 3
                               WHEN 'T2' THEN 2 WHEN 'T1' THEN 1
                           END DESC
                   ) AS rn
            FROM predicciones_ventana
        )
        SELECT pac.id_ciclo, pac.id_parcela, pac.sos,
               pac.t1, pac.t2, pac.t3, pac.eos,
               pac.estado_ciclo, pac.temporada,
               {clasif_col},
               uv.ventana AS ultima_ventana,
               uv.score_compuesto, uv.cultivo_predicho,
               uv.score_pearson, uv.score_magnitud_pendiente,
               uv.rendimiento_estimado_qq_ha
        FROM produccion_acumulada_ciclo pac
        LEFT JOIN ultima_ventana uv
            ON uv.id_ciclo = pac.id_ciclo AND uv.rn = 1
        WHERE {where_sql}
        ORDER BY pac.sos DESC
    """
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=params,
                         parse_dates=["sos", "t1", "t2", "t3", "eos"])
    if col_exists:
        df["clasificacion_final"] = df["clasificacion_final"].fillna("")
    return df


@st.cache_data(show_spinner="Calculando resumen…")
def cargar_resumen_agregado(
    temporada: str,
    ventana: str | None = None,
) -> dict:
    """
    Métricas agregadas para la vista Resumen.
    Solo considera el ciclo más reciente por parcela-temporada.

    Para ciclos finalizados usa el rendimiento y producción real.
    Para activos/candidatos usa la estimación de la ventana más avanzada
    disponible en ``predicciones_ventana``.

    Parámetros
    ----------
    temporada : str
        ``'primera'`` o ``'postrera'``.
    ventana : str | None
        Si se especifica, solo considera predicciones hasta esta ventana
        (T1/T2/T3/EOS). ``None`` = usa la más avanzada disponible.

    Retorna
    -------
    dict con:
        total_parcelas, area_sembrada_ha, total_produccion_qq,
        rendimiento_promedio_qq_ha, rendimiento_ref_qq_ha,
        clasificaciones (dict), distribucion_rendimiento (list),
        ciclos_finalizados, ciclos_activos
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw
    from config import RENDIMIENTO_REF

    orden_ventanas = {"T1": 1, "T2": 2, "T3": 3, "EOS": 4}
    ventana_rank = f"""
        CASE ventana
            WHEN 'EOS' THEN 4 WHEN 'T3' THEN 3
            WHEN 'T2' THEN 2 WHEN 'T1' THEN 1
        END
    """

    if ventana:
        filtro_ventana = f"AND {ventana_rank} <= {orden_ventanas.get(ventana, 4)}"
    else:
        filtro_ventana = ""

    sql = f"""
        WITH ultimo_ciclo AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY id_parcela, temporada
                ORDER BY sos DESC
            ) AS rn
            FROM produccion_acumulada_ciclo
            WHERE temporada = ?
        ),
        ultima_pred AS (
            SELECT id_ciclo, rendimiento_estimado_qq_ha,
                   rendimiento_estimado_qq_parcela,
                   ROW_NUMBER() OVER (
                       PARTITION BY id_ciclo
                       ORDER BY {ventana_rank} DESC
                   ) AS rn
            FROM predicciones_ventana
            WHERE 1=1 {filtro_ventana}
        )
        SELECT uc.id_ciclo, uc.id_parcela, uc.estado_ciclo,
               uc.clasificacion_final,
               COALESCE(uc.rendimiento, up.rendimiento_estimado_qq_ha) AS rendimiento_qq_ha,
               COALESCE(uc.produccion_total, up.rendimiento_estimado_qq_parcela) AS produccion_qq,
               pv.area_ha
        FROM ultimo_ciclo uc
        LEFT JOIN ultima_pred up ON up.id_ciclo = uc.id_ciclo AND up.rn = 1
        LEFT JOIN parcelas_vigentes pv ON pv.id_parcela = uc.id_parcela
        WHERE uc.rn = 1
    """

    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(temporada,))

    if df.empty:
        return {
            "total_parcelas": 0, "area_sembrada_ha": 0.0,
            "total_produccion_qq": 0.0, "rendimiento_promedio_qq_ha": 0.0,
            "rendimiento_ref_qq_ha": RENDIMIENTO_REF.get(temporada, 0),
            "clasificaciones": {}, "distribucion_rendimiento": [],
            "ciclos_finalizados": 0, "ciclos_activos": 0,
        }

    area_real = df.drop_duplicates(subset="id_parcela")["area_ha"].sum()
    vals = df["rendimiento_qq_ha"].dropna()
    prod = df["produccion_qq"].dropna()

    clasif = (
        df["clasificacion_final"]
        .fillna("Sin clasificar")
        .value_counts()
        .to_dict()
    )

    return {
        "total_parcelas": df["id_parcela"].nunique(),
        "area_sembrada_ha": round(area_real, 2),
        "total_produccion_qq": round(prod.sum(), 0),
        "rendimiento_promedio_qq_ha": round(vals.mean(), 1) if not vals.empty else 0.0,
        "rendimiento_ref_qq_ha": RENDIMIENTO_REF.get(temporada, 0),
        "clasificaciones": clasif,
        "distribucion_rendimiento": vals.tolist(),
        "ciclos_finalizados": int((df["estado_ciclo"] == "finalizado").sum()),
        "ciclos_activos": int(df["estado_ciclo"].isin(["activo", "candidato"]).sum()),
    }


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
