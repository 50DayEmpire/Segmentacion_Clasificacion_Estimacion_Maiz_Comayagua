# pipeline/ingesta.py — Ingesta de series temporales EVI/LSWI desde Copernicus Data Space
"""
Descarga y procesa un cubo de datos Sentinel-2 desde openEO (CDSE) para las
parcelas del área de estudio, calculando los índices EVI y LSWI necesarios
para el modelo VPM.

Uso desde terminal (requiere credenciales CDSE configuradas):
    python -m pipeline.ingesta

Uso como módulo:
    from pipeline.ingesta import obtener_datacube_indices_crudo
    dfs = obtener_datacube_indices_crudo(connection, geojson_openeo, "2025-05-01", "2025-10-30")
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import openeo
import pandas as pd
from config import ESCALA, BOA_OFFSET

from utils.dict_a_dataframe import openeo_dict_to_dataframes

# ── Valores por defecto del proceso to_scl_dilation_mask ──────────────────────
# Documentados aquí para que sean fácilmente referenciables y para que
# config_cloud_mask solo tenga que declarar lo que difiere del default.
_CLOUD_MASK_DEFAULTS: dict = {
    "kernel1_size":        21,    # px — primera dilatación (clases mask1_values)
    "kernel2_size":        59,    # px — segunda dilatación (clases mask2_values)
    "mask1_values":        [2, 4, 5, 6, 7],    # SCL: nieve, vegetación, suelo, agua, nubes bajas
    "mask2_values":        [3, 8, 9, 10, 11],  # SCL: sombras, nubes medias/altas, cirrus
    "erosion_kernel_size": 3,     # px — erosión para limpiar bordes de máscara
}


def obtener_datacube_indices_crudo(
    connection: openeo.Connection,
    geojson_openeo: dict,
    fecha_inicio: str,
    fecha_fin: str,
    config_cloud_mask: dict | None = None,
) -> dict:
    """
    Descarga series temporales de EVI y LSWI para un conjunto de parcelas
    usando el backend CDSE de openEO.

    El pipeline aplica:
    1. Máscara morfológica de nubes/sombras (SCL dilation mask).
    2. Carga de bandas B02, B04, B08, B11 con la máscara aplicada.
    3. Interpolación lineal temporal para rellenar píxeles enmascarados.
    4. Cálculo de EVI y LSWI por reducción de dimensión de bandas.
    5. Reducción zonal (media por polígono) y descarga a memoria.

    Parámetros
    ----------
    connection : openeo.Connection
        Conexión activa y autenticada al backend CDSE de openEO.
    geojson_openeo : dict
        GeoJSON con las geometrías de las parcelas en EPSG:4326.
    fecha_inicio : str
        Fecha de inicio del ciclo en formato ISO "YYYY-MM-DD".
        Ejemplo: "2025-05-01" para el ciclo de primera.
    fecha_fin : str
        Fecha de fin del ciclo en formato ISO "YYYY-MM-DD".
        Ejemplo: "2025-10-30" para el ciclo de primera.
    config_cloud_mask : dict | None
        Parámetros opcionales para ``to_scl_dilation_mask``.
        Cualquier clave presente sobreescribe el valor por defecto;
        las claves ausentes conservan los valores de ``_CLOUD_MASK_DEFAULTS``.

        Claves disponibles:

        =====================  =======  ==========================================
        Clave                  Default  Descripción
        =====================  =======  ==========================================
        kernel1_size           21       Tamaño del kernel (px) de la primera
                                        dilatación, aplicada sobre mask1_values.
        kernel2_size           59       Tamaño del kernel (px) de la segunda
                                        dilatación, aplicada sobre mask2_values.
        mask1_values           [2,4,    Clases SCL incluidas en la primera
                               5,6,7]   máscara (nubes densas, vegetación,
                                        suelo desnudo, agua, nubes bajas).
        mask2_values           [3,8,    Clases SCL incluidas en la segunda
                               9,10,11] máscara (sombras de nubes, nubes
                                        medias/altas, cirrus).
        erosion_kernel_size    3        Tamaño del kernel (px) de erosión para
                                        limpiar bordes de la máscara dilatada.
        =====================  =======  ==========================================

        Ejemplo — máscara más agresiva para escenas muy nubosas::

            config_cloud_mask = {
                "kernel1_size": 31,
                "kernel2_size": 81,
                "erosion_kernel_size": 5,
            }

        Ejemplo — excluir sombras de nubes de la segunda máscara::

            config_cloud_mask = {
                "mask2_values": [8, 9, 10, 11],  # sin clase 3 (sombras)
            }

    Retorna
    -------
    dict[str, pd.DataFrame]
        ``{"EVI": DataFrame, "LSWI": DataFrame}``
        DatetimeIndex x columnas de parcelas. Los NaN representan fechas
        con cobertura nubosa persistente; se preservan para que el
        suavizador Whittaker los gestione en la etapa siguiente del pipeline.

    Raises
    ------
    openeo.rest.OpenEoApiError
        Si el backend rechaza alguna operación del grafo de procesos.
    ValueError
        Si el dict retornado por openEO no contiene datos válidos.
    """
    temp_ext = [fecha_inicio, fecha_fin]

    # Mezclar defaults con overrides — config_cloud_mask tiene precedencia
    cm: dict = {**_CLOUD_MASK_DEFAULTS, **(config_cloud_mask or {})}

    # ── 1. Máscara morfológica de nubes y sombras (SCL) ───────────────────────
    print(
        f"☁️  1. Generando máscara de nubes (to_scl_dilation_mask) "
        f"[k1={cm['kernel1_size']}, k2={cm['kernel2_size']}, "
        f"erosion={cm['erosion_kernel_size']}]..."
    )
    scl_cube = connection.load_collection(
        "SENTINEL2_L2A",
        spatial_extent=geojson_openeo,
        temporal_extent=temp_ext,
        bands=["SCL"],
    )

    cloud_mask = scl_cube.process(
        "to_scl_dilation_mask",
        data=scl_cube,
        kernel1_size=cm["kernel1_size"],
        kernel2_size=cm["kernel2_size"],
        mask1_values=cm["mask1_values"],
        mask2_values=cm["mask2_values"],
        erosion_kernel_size=cm["erosion_kernel_size"],
    )

    # ── 2. Cargar bandas ópticas necesarias para VPM ──────────────────────────
    print("🛰️  2. Cargando bandas ópticas (B02 Azul, B04 Rojo, B08 NIR, B11 SWIR)...")
    datacube_vpm = connection.load_collection(
        "SENTINEL2_L2A",
        spatial_extent=geojson_openeo,
        temporal_extent=temp_ext,
        bands=["B02", "B04", "B08", "B11"],
    )

    # IMPORTANTE: Conversión de DN a reflectancia real (0-1) para EVI/LSWI:
    datacube_vpm = datacube_vpm.apply(lambda x: (x - BOA_OFFSET) / ESCALA)

    datacube_limpio = datacube_vpm.mask(cloud_mask)
    datacube_final  = datacube_limpio.mask_polygon(geojson_openeo)

    # ── 3. Interpolación temporal ─────────────────────────────────────────────
    print("🪄  3. Interpolando píxeles enmascarados (interpolación lineal temporal)...")
    datacube_interpolado = datacube_final.apply_dimension(
        dimension="t",
        process="array_interpolate_linear",
    )

    # ── 4. Cálculo de índices EVI y LSWI ──────────────────────────────────────
    print("🧮  4. Calculando EVI y LSWI...")

    def calcular_evi_openeo(data, context=None):
        """EVI = 2.5 × (NIR − Red) / (NIR + 6·Red − 7.5·Blue + 1)"""
        b08 = data.array_element(index=0)  # NIR  — orden de filter_bands: B08,B04,B02
        b04 = data.array_element(index=1)  # Rojo
        b02 = data.array_element(index=2)  # Azul
        return (2.5 * (b08 - b04)) / (b08 + (6.0 * b04) - (7.5 * b02) + 1.0)

    def calcular_lswi_openeo(data, context=None):
        """LSWI = (NIR − SWIR) / (NIR + SWIR)"""
        b08 = data.array_element(index=0)  # NIR  — orden de filter_bands: B08,B11
        b11 = data.array_element(index=1)  # SWIR
        return (b08 - b11) / (b08 + b11)

    evi = (
        datacube_interpolado
        .filter_bands(["B08", "B04", "B02"])
        .reduce_dimension(dimension="bands", reducer=calcular_evi_openeo)
        .add_dimension(name="bands", label="EVI", type="bands")
    )

    lswi = (
        datacube_interpolado
        .filter_bands(["B08", "B11"])
        .reduce_dimension(dimension="bands", reducer=calcular_lswi_openeo)
        .add_dimension(name="bands", label="LSWI", type="bands")
    )

    print("🔗  5. Fusionando cubos EVI y LSWI...")
    datacube_indices = evi.merge_cubes(lswi)

    # ── 5. Reducción zonal y descarga ─────────────────────────────────────────
    print("📊  6. Reducción zonal (media por parcela) en el backend CDSE...")
    cube_promedios = datacube_indices.aggregate_spatial(
        geometries=geojson_openeo,
        reducer="mean",
    )

    print("⏳  7. Descargando series temporales a memoria local...")
    #Fix temporal para ejecutar como batch, ejecución síncrona dejó de funcionar
    job = cube_promedios.execute_batch()
    diccionario_vpm = job.get_results().get_asset().load_json()

    print("🗂️   8. Convirtiendo resultado a DataFrames pandas...")

    # Extraer id_parcela reales del GeoJSON en el mismo orden que se enviaron
    # a aggregate_spatial. openEO respeta el orden de features[], así que
    # anclar la columna al id real evita asignaciones erróneas si el orden
    # cambia entre ejecuciones o si los IDs no son consecutivos.
    ids_parcelas = [
        f["properties"].get("id_parcela", i)
        for i, f in enumerate(geojson_openeo.get("features", []))
    ]
    nombres_columnas = [f"id_{pid}" for pid in ids_parcelas] if ids_parcelas else None

    dfs_vpm = openeo_dict_to_dataframes(
        diccionario=diccionario_vpm,
        nombres_bandas=["EVI", "LSWI"],
        nombres_columnas=nombres_columnas,
    )

    print("✅  Ingesta completada.")

    return dfs_vpm

def _convertir_temperatura(val_raw: float | int | str) -> float:
    """
    AgERA5 entrega temperatura x100 en Kelvin → convertir a °C.
    """
    t_kelvin = float(val_raw) / 100.0 if float(val_raw) > 1000.0 else float(val_raw)
    return t_kelvin - 273.15


def obtener_datos_climaticos_crudo(
    connection: openeo.Connection,
    geojson_openeo: dict,
    fecha_inicio: str,
    fecha_fin: str,
    num_parc: int | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Descarga series temporales de variables meteorológicas (AgERA5)
    para el centroide de la zona de estudio, y las difunde a todas las parcelas.

    El pipeline realiza:
    1. Extracción del centroide regional.
    2. Creación de un bounding box y polígono regional (~11 km de resolución).
    3. Solicitud de cubo de datos 'AGERA5' con las bandas 'temperature-mean' y 'solar-radiation-flux'.
    4. Reducción espacial regional (media) y descarga local a memoria.
    5. Conversión de temperatura de Kelvin (multiplicado por 100) a grados Celsius.
    6. Difusión (broadcast) de la serie de tiempo única a todas las parcelas.

    Parámetros
    ----------
    connection : openeo.Connection
        Conexión activa y autenticada al backend CDSE de openEO.
    geojson_openeo : dict
        GeoJSON con las geometrías de las parcelas en EPSG:4326.
    fecha_inicio : str
        Fecha de inicio del ciclo en formato ISO "YYYY-MM-DD".
    fecha_fin : str
        Fecha de fin del ciclo en formato ISO "YYYY-MM-DD".
    num_parc : int, opcional
        Número de parcelas. Si es None, se infiere del GeoJSON (features).

    Retorna
    -------
    dict[str, pd.DataFrame]
        Un diccionario con dos DataFrames:
        - "temperature-mean": DataFrame con DatetimeIndex x columnas de parcelas.
        - "solar-radiation-flux": DataFrame con DatetimeIndex x columnas de parcelas.
    """
    print("🌍 1. Extrayendo el centroide regional para la grilla climática de AGERA5...")
    try:
        if "features" in geojson_openeo:
            coords = [f["geometry"]["coordinates"] for f in geojson_openeo["features"]]
            lon_centro = coords[0][0][0][0] if isinstance(coords[0][0][0], list) else coords[0][0][0]
            lat_centro = coords[0][0][0][1] if isinstance(coords[0][0][0], list) else coords[0][0][1]
        else:
            lon_centro, lat_centro = -87.6877, 14.4098
    except Exception:
        lon_centro, lat_centro = -87.6877, 14.4098

    bbox_climatico = {
        "west": lon_centro - 0.05,
        "east": lon_centro + 0.05,
        "south": lat_centro - 0.05,
        "north": lat_centro + 0.05
    }

    w, e, s, n = bbox_climatico["west"], bbox_climatico["east"], bbox_climatico["south"], bbox_climatico["north"]
    geojson_aggregate = {
        "type": "Polygon",
        "coordinates": [[
            [w, s],
            [e, s],
            [e, n],
            [w, n],
            [w, s]
        ]]
    }

    temp_ext = [fecha_inicio, fecha_fin]

    print("🌍 2. Solicitando cubo de datos al catálogo federado (AGERA5)...")
    cube_clima = connection.load_collection(
        "AGERA5",
        spatial_extent=bbox_climatico,
        temporal_extent=temp_ext,
        bands=["temperature-mean", "solar-radiation-flux"]
    )

    print("📊 3. Ejecutando reducción espacial sobre el polígono regional GeoJSON...")
    cube_clima_promedios = cube_clima.aggregate_spatial(
        geometries=geojson_aggregate,
        reducer="mean"
    )

    print("⏳ 4. Descargando series de tiempo climáticas...")
    diccionario_clima = cube_clima_promedios.execute()

    fechas_clima = sorted(list(diccionario_clima.keys()))
    print(f"📅 ¡Éxito! Total de fechas recuperadas del servidor federado: {len(fechas_clima)}")

    dfs_clima = openeo_dict_to_dataframes(
        diccionario=diccionario_clima,
        nombres_bandas=["temperature-mean", "solar-radiation-flux"],
        transformaciones={
            "temperature-mean": _convertir_temperatura
        }
    )

    if num_parc is None:
        if isinstance(geojson_openeo, dict) and "features" in geojson_openeo:
            num_parc = len(geojson_openeo["features"])
        else:
            num_parc = 1

    # AgERA5 resolución ~11 km: todas las parcelas caen en el mismo píxel regional.
    # Se hace broadcast explícito de la serie única a todas las parcelas.
    _cols_parcelas = [f"Parcela_{i+1}" for i in range(num_parc)]

    df_t2m = dfs_clima["temperature-mean"].iloc[:, [0] * num_parc].copy()
    df_t2m.columns = _cols_parcelas

    df_ssrd = dfs_clima["solar-radiation-flux"].iloc[:, [0] * num_parc].copy()
    df_ssrd.columns = _cols_parcelas

    print("\n✅ Datos climáticos consolidados de forma segura:")
    print(f"   ✔️ Temperatura Media del Dataset: {df_t2m.mean().mean():.2f} °C")
    print(f"   ✔️ Radiación Media del Dataset: {df_ssrd.mean().mean() / 1e6:.2f} MJ/m²/día")

    return {
        "temperature-mean": df_t2m,
        "solar-radiation-flux": df_ssrd
    }


def cargar_indices_desde_bd(
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    ids_parcelas: list[int] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Consulta ``series_diarias_vpm`` y devuelve los índices crudos en el
    mismo formato que ``obtener_datacube_indices_crudo``, listo para
    pasarse directamente a ``preprocesar_indices_vpm``.

    Parámetros
    ----------
    fecha_inicio : str | None
        Filtro de fecha inicial en formato ``"YYYY-MM-DD"``.
        Si es ``None`` se devuelven todas las fechas disponibles desde
        el registro más antiguo.
    fecha_fin : str | None
        Filtro de fecha final en formato ``"YYYY-MM-DD"``.
        Si es ``None`` se devuelven todas las fechas hasta el registro
        más reciente.
    ids_parcelas : list[int] | None
        Lista de ``id_parcela`` a incluir. Si es ``None`` se devuelven
        todas las parcelas presentes en la tabla.

    Retorna
    -------
    dict[str, pd.DataFrame]
        ``{"EVI": DataFrame, "LSWI": DataFrame}``
        DatetimeIndex × columnas ``"id_<id_parcela>"``, mismo esquema
        que el resultado de ``obtener_datacube_indices_crudo``.

    Raises
    ------
    ValueError
        Si la consulta no devuelve filas (tabla vacía o filtros sin resultados).
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    condiciones: list[str] = []
    params: list = []

    if fecha_inicio:
        condiciones.append("fecha >= ?")
        params.append(fecha_inicio)
    if fecha_fin:
        condiciones.append("fecha <= ?")
        params.append(fecha_fin)
    if ids_parcelas:
        placeholders = ",".join(["?"] * len(ids_parcelas))
        condiciones.append(f"id_parcela IN ({placeholders})")
        params.extend(ids_parcelas)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    sql = f"""
        SELECT id_parcela, fecha, evi_crudo, lswi_crudo
        FROM series_diarias_vpm
        {where}
        ORDER BY fecha, id_parcela;
    """

    with closing(get_connection_raw()) as conn:
        df_raw = pd.read_sql(sql, conn, params=params, parse_dates=["fecha"])

    if df_raw.empty:
        filtros = {
            "fecha_inicio": fecha_inicio,
            "fecha_fin":    fecha_fin,
            "ids_parcelas": ids_parcelas,
        }
        raise ValueError(
            f"La consulta no devolvió filas. Verifica que la tabla 'series_diarias_vpm' "
            f"tenga datos con los filtros aplicados: {filtros}"
        )

    # Normalizar fechas a medianoche (coincide con el formato de ingesta openEO)
    df_raw["fecha"] = df_raw["fecha"].dt.normalize()

    # Nombres de columna en formato "id_<N>" — idéntico al de obtener_datacube_indices_crudo
    df_raw["parcela_col"] = "id_" + df_raw["id_parcela"].astype(str)

    df_evi = df_raw.pivot(index="fecha", columns="parcela_col", values="evi_crudo")
    df_lswi = df_raw.pivot(index="fecha", columns="parcela_col", values="lswi_crudo")

    df_evi.index.name  = None
    df_lswi.index.name = None
    df_evi.columns.name  = None
    df_lswi.columns.name = None

    n_fechas  = len(df_evi)
    n_parcelas = len(df_evi.columns)
    print(
        f"✅  Índices cargados desde BD: {n_fechas} fechas × {n_parcelas} parcelas "
        f"({df_raw['fecha'].min().date()} → {df_raw['fecha'].max().date()})."
    )

    return {"EVI": df_evi, "LSWI": df_lswi}


def cargar_clima_desde_bd(
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    ids_parcelas: list[int] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Consulta ``series_diarias_vpm`` y devuelve los datos climáticos en el
    mismo formato que ``obtener_datos_climaticos_crudo``, listo para
    pasarse directamente a ``calcular_gpp_vpm``.

    AgERA5 tiene resolución ~11 km: todas las parcelas comparten la misma
    serie regional. La función reconstruye el broadcast con columnas
    ``"id_<id_parcela>"`` para mantener consistencia con el resto del pipeline.

    Parámetros
    ----------
    fecha_inicio : str | None
        Filtro de fecha inicial en formato ``"YYYY-MM-DD"``.
        Si es ``None`` se devuelven todas las fechas disponibles desde
        el registro más antiguo.
    fecha_fin : str | None
        Filtro de fecha final en formato ``"YYYY-MM-DD"``.
        Si es ``None`` se devuelven todas las fechas hasta el registro
        más reciente.
    ids_parcelas : list[int] | None
        Lista de ``id_parcela`` a incluir. Si es ``None`` se devuelven
        todas las parcelas presentes en la tabla.

    Retorna
    -------
    dict[str, pd.DataFrame]
        ``{"temperature-mean": DataFrame, "solar-radiation-flux": DataFrame}``
        DatetimeIndex x columnas ``"id_<id_parcela>"``, mismo esquema
        que el resultado de ``obtener_datos_climaticos_crudo``.

    Raises
    ------
    ValueError
        Si la consulta no devuelve filas con datos climáticos (ambas columnas
        NULL) o si los filtros no coinciden con ningún registro.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    condiciones: list[str] = [
        "(temperatura_diaria_promedio IS NOT NULL OR radiacion_total_promedio IS NOT NULL)"
    ]
    params: list = []

    if fecha_inicio:
        condiciones.append("fecha >= ?")
        params.append(fecha_inicio)
    if fecha_fin:
        condiciones.append("fecha <= ?")
        params.append(fecha_fin)
    if ids_parcelas:
        placeholders = ",".join(["?"] * len(ids_parcelas))
        condiciones.append(f"id_parcela IN ({placeholders})")
        params.extend(ids_parcelas)

    where = f"WHERE {' AND '.join(condiciones)}"

    sql = f"""
        SELECT id_parcela, fecha,
               temperatura_diaria_promedio,
               radiacion_total_promedio
        FROM series_diarias_vpm
        {where}
        ORDER BY fecha, id_parcela;
    """

    with closing(get_connection_raw()) as conn:
        df_raw = pd.read_sql(sql, conn, params=params, parse_dates=["fecha"])

    if df_raw.empty:
        filtros = {
            "fecha_inicio": fecha_inicio,
            "fecha_fin":    fecha_fin,
            "ids_parcelas": ids_parcelas,
        }
        raise ValueError(
            f"La consulta no devolvió filas con datos climáticos. "
            f"Verifica que la tabla 'series_diarias_vpm' tenga datos climáticos "
            f"con los filtros aplicados: {filtros}"
        )

    df_raw["fecha"] = df_raw["fecha"].dt.normalize()
    df_raw["parcela_col"] = "id_" + df_raw["id_parcela"].astype(str)

    df_temp = df_raw.pivot(
        index="fecha", columns="parcela_col", values="temperatura_diaria_promedio"
    )
    df_rad = df_raw.pivot(
        index="fecha", columns="parcela_col", values="radiacion_total_promedio"
    )

    for df in (df_temp, df_rad):
        df.index.name   = None
        df.columns.name = None

    n_fechas   = len(df_temp)
    n_parcelas = len(df_temp.columns)
    print(
        f"✅  Clima cargado desde BD: {n_fechas} fechas × {n_parcelas} parcelas "
        f"({df_raw['fecha'].min().date()} → {df_raw['fecha'].max().date()})."
    )

    return {
        "temperature-mean":      df_temp,
        "solar-radiation-flux":  df_rad,
    }


# ══════════════════════════════════════════════════════════════════════════════
# CAPA DE CACHÉ — obtener con cobertura parcial de BD + relleno openEO
# ══════════════════════════════════════════════════════════════════════════════

def _detectar_gaps(
    fecha_inicio: str,
    fecha_fin: str,
    fechas_en_bd: pd.DatetimeIndex,
) -> list[tuple[str, str]]:
    """
    Calcula los sub-rangos del período [fecha_inicio, fecha_fin] que NO están
    cubiertos por ``fechas_en_bd``.

    ``fechas_en_bd`` debe representar **fechas que ya fueron consultadas a openEO**,
    independientemente de si los valores son NULL (nubes) o no. Una fecha con
    todos los valores NULL fue consultada y openEO no tenía datos útiles para ella;
    no debe volver a pedirse.

    Trabaja a granularidad de día. Devuelve una lista de tuplas
    ``(gap_inicio, gap_fin)`` en formato "YYYY-MM-DD", listas para pasarse
    a openEO como ``temporal_extent``.

    Ejemplos
    --------
    Período pedido: 2025-01-01 → 2025-01-10
    BD cubre      : 2025-01-05 → 2025-01-10  (con o sin NaN en valores)
    Gaps           : [("2025-01-01", "2025-01-04")]

    Período pedido: 2025-01-01 → 2025-01-10
    BD cubre      : 2025-01-03 → 2025-01-07
    Gaps           : [("2025-01-01", "2025-01-02"), ("2025-01-08", "2025-01-10")]

    Período pedido: 2025-01-01 → 2025-01-10
    BD cubre      : (vacío)
    Gaps           : [("2025-01-01", "2025-01-10")]
    """
    rango_completo = pd.date_range(fecha_inicio, fecha_fin, freq="D")

    if fechas_en_bd.empty:
        return [(fecha_inicio, fecha_fin)]

    fechas_faltantes = rango_completo.difference(fechas_en_bd.normalize())

    if fechas_faltantes.empty:
        return []

    # Agrupar fechas contiguas en sub-rangos
    gaps: list[tuple[str, str]] = []
    bloque_inicio = fechas_faltantes[0]
    bloque_prev   = fechas_faltantes[0]

    for fecha in fechas_faltantes[1:]:
        if (fecha - bloque_prev).days == 1:
            bloque_prev = fecha
        else:
            gaps.append((bloque_inicio.strftime("%Y-%m-%d"), bloque_prev.strftime("%Y-%m-%d")))
            bloque_inicio = fecha
            bloque_prev   = fecha

    gaps.append((bloque_inicio.strftime("%Y-%m-%d"), bloque_prev.strftime("%Y-%m-%d")))
    return gaps


def _fechas_consultadas_indices(
    fecha_inicio: str,
    fecha_fin: str,
    ids_parcelas: list[int] | None = None,
) -> pd.DatetimeIndex:
    """
    Devuelve las fechas que **ya fueron enviadas a openEO** para índices,
    independientemente de si ``evi_crudo`` / ``lswi_crudo`` son NULL.

    Una fila con valores NULL significa que openEO respondió sin datos útiles
    (nubosidad total); la fecha fue consultada y no debe pedirse de nuevo.

    Se considera que una fecha está cubierta si existe al menos un registro
    ``(id_parcela, fecha)`` en ``series_diarias_vpm`` para alguna parcela del
    conjunto, sin importar si los valores de índice son NULL.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    condiciones = ["fecha BETWEEN ? AND ?"]
    params: list = [fecha_inicio, fecha_fin]

    if ids_parcelas:
        ph = ",".join(["?"] * len(ids_parcelas))
        condiciones.append(f"id_parcela IN ({ph})")
        params.extend(ids_parcelas)

    sql = f"""
        SELECT DISTINCT fecha
        FROM series_diarias_vpm
        WHERE {' AND '.join(condiciones)}
        ORDER BY fecha;
    """

    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=params, parse_dates=["fecha"])

    return df["fecha"].dt.normalize() if not df.empty else pd.DatetimeIndex([])


def _fechas_consultadas_clima(
    fecha_inicio: str,
    fecha_fin: str,
    ids_parcelas: list[int] | None = None,
) -> pd.DatetimeIndex:
    """
    Devuelve las fechas que **ya fueron enviadas a openEO** para clima,
    independientemente de si ``temperatura_diaria_promedio`` / ``radiacion_total_promedio``
    son NULL.

    Una fila con ambos valores climáticos NULL significa que openEO respondió
    sin datos; la fecha fue consultada y no debe pedirse de nuevo.

    Se considera que una fecha está cubierta si existe al menos un registro
    ``(id_parcela, fecha)`` en ``series_diarias_vpm`` para alguna parcela.

    Nota: a diferencia de los índices, el clima no produce NULLs por nubes
    (AgERA5 es un reanálisis sin huecos), pero se aplica la misma lógica
    por consistencia y robustez.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    condiciones = [
        "fecha BETWEEN ? AND ?",
        "(temperatura_diaria_promedio IS NOT NULL OR radiacion_total_promedio IS NOT NULL)",
    ]
    params: list = [fecha_inicio, fecha_fin]

    if ids_parcelas:
        ph = ",".join(["?"] * len(ids_parcelas))
        condiciones.append(f"id_parcela IN ({ph})")
        params.extend(ids_parcelas)

    sql = f"""
        SELECT DISTINCT fecha
        FROM series_diarias_vpm
        WHERE {' AND '.join(condiciones)}
        ORDER BY fecha;
    """

    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=params, parse_dates=["fecha"])

    return df["fecha"].dt.normalize() if not df.empty else pd.DatetimeIndex([])


def obtener_indices(
    connection: openeo.Connection,
    geojson_openeo: dict,
    fecha_inicio: str,
    fecha_fin: str,
    config_cloud_mask: dict | None = None,
    forzar_descarga: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Versión con caché de ``obtener_datacube_indices_crudo``.

    Consulta la BD primero y solo descarga de openEO las fechas que faltan.
    Si la BD cubre el rango completo no se realiza ninguna petición a openEO.

    Lógica
    ------
    1. Consultar BD para el rango [fecha_inicio, fecha_fin].
    2. Detectar sub-rangos sin cobertura (gaps).
    3. Para cada gap, llamar a ``obtener_datacube_indices_crudo`` y persistir
       en BD con ``guardar_indices_crudos``.
    4. Consolidar datos de BD + descargados y devolver el rango completo.

    Parámetros
    ----------
    connection : openeo.Connection
        Conexión activa al backend CDSE. Solo se usa si hay gaps.
    geojson_openeo : dict
        GeoJSON FeatureCollection con las parcelas en EPSG:4326.
    fecha_inicio : str
        Inicio del rango en formato "YYYY-MM-DD".
    fecha_fin : str
        Fin del rango en formato "YYYY-MM-DD".
    config_cloud_mask : dict | None
        Overrides para la máscara SCL. Igual que en ``obtener_datacube_indices_crudo``.
    forzar_descarga : bool
        Si ``True``, ignora la BD y descarga el rango completo desde openEO,
        sobreescribiendo los datos existentes (``mode="replace"``).

    Retorna
    -------
    dict[str, pd.DataFrame]
        ``{"EVI": DataFrame, "LSWI": DataFrame}``
        Mismo esquema que ``obtener_datacube_indices_crudo``.
    """
    from utils.db import guardar_indices_crudos

    if forzar_descarga:
        print(f"🔄  forzar_descarga=True — descargando [{fecha_inicio} → {fecha_fin}] completo desde openEO...")
        dfs = obtener_datacube_indices_crudo(connection, geojson_openeo, fecha_inicio, fecha_fin, config_cloud_mask)
        guardar_indices_crudos(dfs, mode="replace")
        return dfs

    # ── 1. Consultar qué fechas ya fueron enviadas a openEO (con o sin NaN) ───
    # No se usa el DataFrame de valores para detectar cobertura: una fecha
    # nublada tiene fila en BD con evi_crudo=NULL y NO debe re-consultarse.
    fechas_cubiertas = _fechas_consultadas_indices(fecha_inicio, fecha_fin)

    # ── 2. Detectar gaps ──────────────────────────────────────────────────────
    gaps = _detectar_gaps(fecha_inicio, fecha_fin, fechas_cubiertas)

    if not gaps:
        print(f"✅  Índices: BD cubre el rango completo [{fecha_inicio} → {fecha_fin}]. Sin descarga openEO.")
        # Cargar los valores (incluye NaN por nubes) para devolver el DataFrame completo
        try:
            return cargar_indices_desde_bd(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
        except ValueError:
            # Todas las filas existen pero todos los valores son NaN — devolver igual
            return cargar_indices_desde_bd(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)

    print(f"📡  Índices: {len(gaps)} gap(s) detectado(s) → se descargarán de openEO:")
    for g_ini, g_fin in gaps:
        print(f"     • {g_ini} → {g_fin}")

    # ── 3. Descargar cada gap y persistir ─────────────────────────────────────
    for g_ini, g_fin in gaps:
        print(f"\n🛰️  Descargando gap [{g_ini} → {g_fin}]...")
        dfs_gap = obtener_datacube_indices_crudo(
            connection, geojson_openeo, g_ini, g_fin, config_cloud_mask
        )
        guardar_indices_crudos(dfs_gap, mode="append")

    # ── 4. Recargar desde BD (fuente de verdad) y devolver rango completo ─────
    # Se recarga desde BD en lugar de consolidar en memoria para garantizar que
    # lo devuelto es exactamente lo que quedó persistido, incluyendo NaN por nubes.
    return cargar_indices_desde_bd(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)


def obtener_clima(
    connection: openeo.Connection,
    geojson_openeo: dict,
    fecha_inicio: str,
    fecha_fin: str,
    num_parc: int | None = None,
    forzar_descarga: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Versión con caché de ``obtener_datos_climaticos_crudo``.

    Consulta la BD primero y solo descarga de openEO las fechas que faltan.
    Si la BD cubre el rango completo no se realiza ninguna petición a openEO.

    Lógica
    ------
    1. Consultar BD para el rango [fecha_inicio, fecha_fin].
    2. Detectar sub-rangos sin cobertura (gaps).
    3. Para cada gap, llamar a ``obtener_datos_climaticos_crudo`` y persistir
       en BD con ``guardar_datos_climaticos``.
    4. Consolidar datos de BD + descargados y devolver el rango completo.

    Parámetros
    ----------
    connection : openeo.Connection
        Conexión activa al backend federado (AgERA5). Solo se usa si hay gaps.
    geojson_openeo : dict
        GeoJSON FeatureCollection con las parcelas en EPSG:4326.
    fecha_inicio : str
        Inicio del rango en formato "YYYY-MM-DD".
    fecha_fin : str
        Fin del rango en formato "YYYY-MM-DD".
    num_parc : int | None
        Número de parcelas. Si None se infiere del GeoJSON.
    forzar_descarga : bool
        Si ``True``, ignora la BD y descarga el rango completo desde openEO,
        sobreescribiendo los datos existentes (``mode="replace"``).

    Retorna
    -------
    dict[str, pd.DataFrame]
        ``{"temperature-mean": DataFrame, "solar-radiation-flux": DataFrame}``
        Mismo esquema que ``obtener_datos_climaticos_crudo``.
    """
    from utils.db import guardar_datos_climaticos

    if forzar_descarga:
        print(f"🔄  forzar_descarga=True — descargando [{fecha_inicio} → {fecha_fin}] completo desde openEO...")
        dfs = obtener_datos_climaticos_crudo(connection, geojson_openeo, fecha_inicio, fecha_fin, num_parc)
        guardar_datos_climaticos(dfs, mode="replace")
        return dfs

    # ── 1. Consultar qué fechas ya fueron enviadas a openEO ───────────────────
    # Para clima (AgERA5) no hay NaN por nubes, pero usamos la misma lógica:
    # solo fechas con al menos un valor climático no-NULL cuentan como cubiertas.
    fechas_cubiertas = _fechas_consultadas_clima(fecha_inicio, fecha_fin)

    # ── 2. Detectar gaps ──────────────────────────────────────────────────────
    gaps = _detectar_gaps(fecha_inicio, fecha_fin, fechas_cubiertas)

    if not gaps:
        print(f"✅  Clima: BD cubre el rango completo [{fecha_inicio} → {fecha_fin}]. Sin descarga openEO.")
        return cargar_clima_desde_bd(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)

    print(f"🌤️  Clima: {len(gaps)} gap(s) detectado(s) → se descargarán de openEO:")
    for g_ini, g_fin in gaps:
        print(f"     • {g_ini} → {g_fin}")

    # ── 3. Descargar cada gap y persistir ─────────────────────────────────────
    for g_ini, g_fin in gaps:
        print(f"\n🌍  Descargando gap climático [{g_ini} → {g_fin}]...")
        dfs_gap = obtener_datos_climaticos_crudo(
            connection, geojson_openeo, g_ini, g_fin, num_parc
        )
        guardar_datos_climaticos(dfs_gap, mode="append")

    # ── 4. Recargar desde BD (fuente de verdad) y devolver rango completo ─────
    return cargar_clima_desde_bd(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)


if __name__ == "__main__":
    import json
    from pathlib import Path
    from config import OPENEO, OPENEOFED

    GEOJSON_PATH = Path(__file__).parent.parent / "data" / "PoligonosMaizPlayitas.geojson"
    gdf = gpd.read_file(str(GEOJSON_PATH)).to_crs("EPSG:4326")
    geojson_dict = json.loads(gdf.to_json())

    # Índices → CDSE
    conn_cdse = openeo.connect(f"https://{OPENEO}").authenticate_oidc()
    # Clima   → backend federado
    conn_fed  = openeo.connect(f"https://{OPENEOFED}").authenticate_oidc()

    # Ejemplo con máscara personalizada para escena muy nubosa
    dfs = obtener_datacube_indices_crudo(
        connection=conn_cdse,
        geojson_openeo=geojson_dict,
        fecha_inicio="2025-05-01",
        fecha_fin="2025-10-30",
        config_cloud_mask={
            "kernel1_size": 31,
            "kernel2_size": 81,
            "erosion_kernel_size": 5,
        },
    )

    for banda, df in dfs.items():
        print(f"\n{banda}: {df.shape[0]} fechas x {df.shape[1]} parcelas")
        print(df.head())

    dfs_clima = obtener_datos_climaticos_crudo(
        connection=conn_fed,
        geojson_openeo=geojson_dict,
        fecha_inicio="2025-05-01",
        fecha_fin="2025-10-30",
    )

    for banda, df in dfs_clima.items():
        print(f"\n{banda}: {df.shape[0]} fechas x {df.shape[1]} parcelas")
        print(df.head())
