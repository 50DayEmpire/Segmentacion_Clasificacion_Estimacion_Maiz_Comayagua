# pipeline/modulo_historico.py — Ingesta y procesamiento histórico multianual
"""
Módulo para poblar y procesar datos históricos completos desde
ANIO_INICIAL_HISTORICO hasta la fecha actual.

Flujo
-----
1. Conectar a openEO (CDSE para Sentinel-2, federado para AgERA5).
2. Cargar GeoJSON de parcelas desde la BD.
3. Ingestar índices (EVI, LSWI) con ``obtener_indices``.
4. Ingestar datos climáticos (temperatura, radiación) con ``obtener_clima``.
5. Preprocesar índices (filtro de outliers, reindexado diario, suavizado Whittaker).
6. Segmentar ciclos fenológicos por parcela con ``segmentar_ciclos``.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import openeo
import pandas as pd

import numpy as np

from config import (
    ANIO_INICIAL_HISTORICO,
    GPKG_PATH,
    LAYERS_GPKG,
    OPENEO,
    OPENEOFED,
    DIAS_VENTANAS,
)
from pipeline.ingesta import obtener_indices_por_lotes, obtener_clima_por_lotes, cargar_indices_desde_bd, cargar_clima_desde_bd
from contextlib import closing

from utils.aplicar_whittaker import aplicar_whittaker_series
from utils.conexionDB import get_connection_raw
from pipeline.modulo_fenologico import segmentar_ciclos, detectar_sos, crear_ciclo_historico
from pipeline.modulo_predictivo import construir_climatologia_diaria, guardar_climatologia_diaria

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _crear_logger_seed() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    nombre = "seed_historico_offline"
    log_path = LOGS_DIR / f"{nombre}.log"
    logger = logging.getLogger(nombre)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        fh = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    return logger


# =============================================================================
# Funciones auxiliares
# =============================================================================

def _conectar_cdse() -> openeo.Connection:
    """Crea conexión autenticada al backend CDSE de Copernicus Data Space."""
    return openeo.connect(f"https://{OPENEO}").authenticate_oidc()


def _conectar_fed() -> openeo.Connection:
    """Crea conexión autenticada al backend federado (AgERA5)."""
    return openeo.connect(f"https://{OPENEOFED}").authenticate_oidc()


def _cargar_geojson_parcelas() -> dict:
    """Carga el GeoJSON de todas las parcelas desde el GeoPackage."""
    gdf = (
        gpd.read_file(str(GPKG_PATH), layer=LAYERS_GPKG["parcelas"])
        .to_crs("EPSG:4326")
    )
    return json.loads(gdf.to_json())


# =============================================================================
# LSWI máximo histórico
# =============================================================================

def seed_lswi_max(
    df_lswi_crudo: pd.DataFrame,
    segmentos_por_parcela: dict[int, list[tuple[pd.Timestamp, pd.Timestamp]]],
    lambda_param: float = 4000.0,
) -> int:
    """
    Calcula el LSWI máximo por parcela a partir de **todos** los segmentos
    (ciclos) detectados.  Asigna cada segmento a su temporada
    (``primera``/``postrera``) según el mes del punto medio del segmento
    y persiste el máximo histórico por parcela-temporada en
    ``lswi_maximo``.
    Cada segmento se suaviza de forma independiente con Whittaker antes
    de extraer su LSWI máximo.

    Parámetros
    ----------
    df_lswi_crudo : pd.DataFrame
        DataFrame con índices crudos de LSWI (fechas de observación como
        índice, columnas ``id_<parcela>``).
    segmentos_por_parcela : dict[int, list[tuple[Timestamp, Timestamp]]]
        Segmentos detectados por ``segmentar_ciclos``.
    lambda_param : float
        Parámetro de suavizado Whittaker (default 4000.0).

    Retorna
    -------
    int
        Número de filas insertadas en ``lswi_maximo``.
    """
    log = _crear_logger_seed()
    filas = []
    for id_parcela, segmentos in segmentos_por_parcela.items():
        col = f"id_{id_parcela}"
        if col not in df_lswi_crudo.columns:
            continue

        if not segmentos:
            continue

        maximos_por_temporada: dict[str, list[float]] = {}
        for inicio, fin in segmentos:
            raw = df_lswi_crudo.loc[inicio:fin, col].dropna()
            if raw.empty:
                continue

            raw = raw.where((raw >= -1.0) & (raw <= 1.0), np.nan).dropna()
            if raw.empty:
                continue

            diario = raw.to_frame(col).reindex(
                pd.date_range(inicio, fin, freq="D"),
            )
            suave = aplicar_whittaker_series(
                {"LSWI": diario},
                lambda_param=lambda_param,
            )["LSWI"]
            lswi_max_seg = float(suave[col].max())

            punto_medio = inicio + (fin - inicio) / 2
            mes = punto_medio.month
            temporada = "primera" if 4 <= mes <= 7 else "postrera"
            maximos_por_temporada.setdefault(temporada, []).append(lswi_max_seg)

        for temporada, valores in maximos_por_temporada.items():
            max_global = max(valores)
            filas.append((id_parcela, max_global, temporada))

    if not filas:
        log.warning("[WAVE] No se pudieron calcular valores de LSWI máximo.")
        return 0

    sql = """
        INSERT INTO lswi_maximo (id_parcela, lswi_max, temporada)
        VALUES (?, ?, ?)
        ON CONFLICT (id_parcela, temporada) DO UPDATE SET
            lswi_max = excluded.lswi_max
    """
    with closing(get_connection_raw()) as conn:
        with conn:
            conn.executemany(sql, filas)

    log.info("    → LSWI máximo persistido para %d parcela(s)-temporada(s).", len(filas))
    return len(filas)


# =============================================================================
# Consulta de LSWI máximo del último ciclo por temporada
# =============================================================================

def obtener_lswi_max_ultimo_ciclo(
    id_parcela: int,
    temporada: str,
) -> float | None:
    """
    Obtiene el ``lswi_max`` del ciclo más reciente en
    ``produccion_acumulada_ciclo`` para la parcela y temporada dadas.

    Parámetros
    ----------
    id_parcela : int
        Identificador de la parcela.
    temporada : str
        ``"primera"`` o ``"postrera"``.

    Retorna
    -------
    float | None
        El valor de ``lswi_max`` o ``None`` si no hay ciclos registrados.
    """
    sql = """
        SELECT lswi_max
        FROM produccion_acumulada_ciclo
        WHERE id_parcela = ? AND temporada = ?
        ORDER BY id_ciclo DESC
        LIMIT 1
    """
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(id_parcela, temporada))
    if df.empty or df.iloc[0]["lswi_max"] is None:
        return None
    return float(df.iloc[0]["lswi_max"])


# =============================================================================
# Función principal
# =============================================================================

def seed_series_historicas(
    fecha_fin: str | None = None,
    lambda_param: float = 4000.0,
    distancia_min_dias: int = 70,
    prominencia_min: float = 0.05,
) -> dict:
    """
    Pobla y procesa todos los datos históricos (índices, clima, ciclos).

    Pasos
    -----
    1. Conecta a openEO (CDSE + federado).
    2. Carga el GeoJSON de parcelas desde el GPKG.
    3. Ingesta índices EVI/LSWI (Sentinel-2) desde ANIO_INICIAL_HISTORICO
       hasta *fecha_fin* (hoy por defecto).
    4. Ingesta datos climáticos (AgERA5) para el mismo período.
    5. Preprocesa los índices (filtro de outliers, reindexado diario,
       suavizado Whittaker) mediante ``preprocesar_indices_vpm``.
    6. Segmenta ciclos fenológicos por parcela mediante ``segmentar_ciclos``
       sobre la serie suavizada de EVI.

    Parámetros
    ----------
    fecha_fin : str, opcional
        Fecha final del período en formato "YYYY-MM-DD".
        Por defecto: fecha actual.
    lambda_param : float, opcional
        Parámetro de suavizado Whittaker (default 4000.0).
    distancia_min_dias : int, opcional
        Separación mínima entre valles para segmentación (default 70).
    prominencia_min : float, opcional
        Profundidad mínima del valle para segmentación (default 0.05).

    Retorna
    -------
    dict
        ``{
            "indices_crudos": {"EVI": DataFrame, "LSWI": DataFrame},
            "clima": {"temperature-mean": DataFrame, "solar-radiation-flux": DataFrame},
            "indices_procesados": {"EVI": DataFrame, "LSWI": DataFrame, ...},
            "segmentos_por_parcela": {id_parcela: [(inicio, fin), ...]},
        }``
    """
    # ── 0. Definir rango de fechas ─────────────────────────────────────────
    if fecha_fin is None:
        fecha_fin = date.today().isoformat()

    fecha_inicio = f"{ANIO_INICIAL_HISTORICO}-01-01"

    print(f"[CAL]  Rango histórico: [{fecha_inicio} → {fecha_fin}]", flush=True)
    print(f"[CON]  Conectando a openEO CDSE y federado...", flush=True)

    # ── 1. Conexiones openEO ───────────────────────────────────────────────
    conn_cdse = _conectar_cdse()
    conn_fed = _conectar_fed()

    # ── 2. GeoJSON de parcelas ─────────────────────────────────────────────
    print(f"[MAP]   Cargando GeoJSON de parcelas desde {GPKG_PATH}...")
    geojson = _cargar_geojson_parcelas()
    n_parcelas = len(geojson.get("features", []))
    print(f"    → {n_parcelas} parcela(s) cargada(s).")

    # ── 3. Ingesta de índices Sentinel-2 (por lotes anuales) ──────────────
    print(f"\n[SAT]   Ingestando índices EVI/LSWI desde {fecha_inicio}...")
    dfs_indices = obtener_indices_por_lotes(
        connection=conn_cdse,
        geojson_openeo=geojson,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )
    print(f"    → EVI: {dfs_indices['EVI'].shape[0]} fechas, "
          f"{dfs_indices['EVI'].shape[1]} parcelas")
    print(f"    → LSWI: {dfs_indices['LSWI'].shape[0]} fechas, "
          f"{dfs_indices['LSWI'].shape[1]} parcelas")

    # ── 4. Ingesta de datos climáticos (por lotes anuales) ────────────────
    print(f"\n[TEMP]  Ingestando datos climáticos (AgERA5) desde {fecha_inicio}...")
    dfs_clima = obtener_clima_por_lotes(
        connection=conn_fed,
        geojson_openeo=geojson,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        num_parc=n_parcelas,
    )
    print(f"    → Temperatura: {dfs_clima['temperature-mean'].shape[0]} fechas")
    print(f"    → Radiación: {dfs_clima['solar-radiation-flux'].shape[0]} fechas")

    # ── 5. Climatología ──────────────────────────────────────────────────
    print(f"\n[CHART]  Calculando y persistiendo climatología...")

    df_temp = dfs_clima["temperature-mean"]
    df_ssrd = dfs_clima["solar-radiation-flux"]

    serie_temp = df_temp.iloc[:, 0].dropna()
    serie_rad = df_ssrd.iloc[:, 0].dropna()

    clima_temp = construir_climatologia_diaria(serie_temp)
    clima_rad = construir_climatologia_diaria(serie_rad)

    anio_min = serie_temp.index.year.min()
    anio_max = serie_temp.index.year.max()

    guardar_climatologia_diaria(clima_rad, clima_temp, anio_min, anio_max)
    print(f"    → Climatología persistida (años {anio_min}-{anio_max}).")

    # ── 6. Suavizado Whittaker directo sobre EVI ──────────────────────────
    print(f"\n[GEAR]   Suavizando EVI con Whittaker-Eilers...")
    df_evi = dfs_indices["EVI"].copy()

    df_evi = df_evi.mask((df_evi < -1.0) | (df_evi > 1.0), np.nan)

    rango_diario = pd.date_range(
        start=df_evi.index.min(),
        end=df_evi.index.max(),
        freq="D",
    )
    df_evi = df_evi.reindex(rango_diario)

    dfs_suavizado = aplicar_whittaker_series(
        {"EVI": df_evi},
        lambda_param=lambda_param,
    )
    df_evi = dfs_suavizado["EVI"]
    print(f"    → EVI suavizado: {df_evi.shape[0]} días, "
          f"{df_evi.shape[1]} parcelas")
    print(f"Suavizado con lambda={lambda_param}")

    # ── 8. Segmentación de ciclos por parcela ─────────────────────────────
    print(f"\n[SEED]  Segmentando ciclos fenológicos por parcela...")
    segmentos_por_parcela: dict[int, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}

    for col in df_evi.columns:
        try:
            id_parcela = int(col.split("_")[1])
        except (IndexError, ValueError):
            continue

        serie = df_evi[col].dropna()
        if serie.empty:
            continue

        segmentos = segmentar_ciclos(
            serie=serie,
            distancia_min_dias=distancia_min_dias,
            prominencia_min=prominencia_min,
        )
        segmentos_por_parcela[id_parcela] = segmentos

    n_con_segmentos = len(segmentos_por_parcela)
    total_segmentos = sum(len(v) for v in segmentos_por_parcela.values())
    print(f"    → {n_con_segmentos} parcela(s) con segmentos detectados "
          f"({total_segmentos} ciclos en total).")

    # ── 9. LSWI máximo histórico (todos los segmentos) ────────────────────
    print(f"\n[WAVE]  Calculando LSWI máximo desde TODOS los segmentos...")
    seed_lswi_max(
        dfs_indices["LSWI"],
        segmentos_por_parcela,
        lambda_param=lambda_param,
    )

    # ── 10. Detección de SOS por segmento y persistencia ───────────────────
    print(f"\n[SEED]  Detectando SOS en cada segmento...")
    sos_por_segmento: dict[int, list[dict]] = {}
    sos_detectados = 0
    ciclos_creados = 0
    ciclos_creados_info: list[dict] = []

    for id_parcela, segmentos in segmentos_por_parcela.items():
        col = f"id_{id_parcela}"
        if col not in df_evi.columns:
            continue

        lista_resultados = []
        for inicio, fin in segmentos:
            serie_seg = df_evi.loc[inicio:fin, col].dropna()
            if serie_seg.empty:
                continue

            resultado = detectar_sos(
                serie=serie_seg.values,
                fechas=serie_seg.index,
                factor=0.2,
                ventana_busqueda=(inicio, fin),
            )
            resultado["inicio_segmento"] = inicio
            resultado["fin_segmento"] = fin
            lista_resultados.append(resultado)

            sos_fecha = resultado.get("sos_fecha")
            if sos_fecha is not None:
                sos_detectados += 1
                id_ciclo = crear_ciclo_historico(
                    id_parcela=id_parcela,
                    sos_fecha=sos_fecha,
                    fecha_inicio=inicio,
                    fecha_fin=fin,
                )
                ciclos_creados += 1
                eos_fecha_calc = sos_fecha + pd.Timedelta(days=DIAS_VENTANAS["eos"])
                ciclos_creados_info.append({
                    "id_ciclo": id_ciclo,
                    "id_parcela": id_parcela,
                    "sos_fecha": sos_fecha,
                    "eos_fecha": eos_fecha_calc,
                })

        if lista_resultados:
            sos_por_segmento[id_parcela] = lista_resultados

    print(f"    → SOS detectado en {sos_detectados}/{total_segmentos} segmentos "
          f"({len(sos_por_segmento)} parcelas).")
    print(f"    → {ciclos_creados} ciclo(s) histórico(s) creado(s) en BD.")

    # ── 11. Predicciones por ventana para cada ciclo histórico ─────────────
    if ciclos_creados_info:
        from datetime import date as _date
        from config import VENTANAS, DIAS_VENTANAS as _DV
        from pipeline.flujos_trabajo import ejecutar_prediccion_ventana

        hoy = _date.today()
        print(f"\n[PRED] Generando predicciones para {len(ciclos_creados_info)} ciclo(s)...")
        predicciones_ok = 0
        for cinfo in ciclos_creados_info:
            for ventana in VENTANAS:
                if ventana == "EOS":
                    fecha_ventana = cinfo["eos_fecha"]
                else:
                    fecha_ventana = cinfo["sos_fecha"] + pd.Timedelta(days=_DV[ventana])
                if fecha_ventana.date() > hoy:
                    continue
                res = ejecutar_prediccion_ventana(
                    id_ciclo=cinfo["id_ciclo"],
                    ventana=ventana,
                    fecha_hoy=hoy,
                    lambda_param=lambda_param,
                )
                if res is not None:
                    predicciones_ok += 1
        print(f"    → {predicciones_ok} prediccion(es) generada(s) exitosamente.")
    else:
        print(f"\n[PRED] Sin ciclos creados, se omite generación de predicciones.")

    # ── Resultado ──────────────────────────────────────────────────────────
    return {
        "indices_crudos": dfs_indices,
        "clima": dfs_clima,
        "evi_suavizado": df_evi,
        "segmentos_por_parcela": segmentos_por_parcela,
        "sos_por_segmento": sos_por_segmento,
    }


def seed_historico_offline(
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    lambda_param: float = 4000.0,
    distancia_min_dias: int = 70,
    prominencia_min: float = 0.05,
    factor_sos: float = 0.2,
) -> dict:
    """
    Seed histórico OFFLINE: lee índices y clima desde la BD local (sin openEO),
    elimina y recalcula ciclos, SOS, LSWI max, climatología y predicciones.

    No toca ``series_diarias_vpm`` (índices crudos ni datos climáticos).

    Parámetros
    ----------
    fecha_inicio : str, opcional
        Fecha inicial del rango a procesar ("YYYY-MM-DD").
        Por defecto: ``ANIO_INICIAL_HISTORICO-01-01``.
    fecha_fin : str, opcional
        Fecha final del rango a procesar ("YYYY-MM-DD").
        Por defecto: fecha actual.
    lambda_param : float, opcional
        Parámetro de suavizado Whittaker (default 4000.0).
    distancia_min_dias : int, opcional
        Separación mínima entre valles para segmentación (default 70).
    prominencia_min : float, opcional
        Profundidad mínima del valle para segmentación (default 0.05).
    factor_sos : float, opcional
        Fracción de la amplitud usada como umbral SOS (default 0.2).

    Retorna
    -------
    dict con las mismas claves que ``seed_series_historicas``.
    """
    log = _crear_logger_seed()
    if fecha_fin is None:
        fecha_fin = date.today().isoformat()
    if fecha_inicio is None:
        fecha_inicio = f"{ANIO_INICIAL_HISTORICO}-01-01"

    log.info("[OFFLINE] Rango: [%s → %s]", fecha_inicio, fecha_fin)

    # ── 1. Eliminar cálculos anteriores (replace) ─────────────────────────
    log.info("[CLEAN] Eliminando cálculos previos...")
    tablas_orden = [
        "series_extrapoladas_ventana",
        "predicciones_ventana",
        "indices_suavizados",
        "produccion_acumulada_ciclo",
        "lswi_maximo",
        "climatologia_diaria",
    ]
    with closing(get_connection_raw()) as conn:
        with conn:
            for tabla in tablas_orden:
                n = conn.execute(f"DELETE FROM {tabla}").rowcount
                log.info("    → %s: %d fila(s) eliminada(s).", tabla, n)

    # ── 2. Cargar índices crudos desde BD ────────────────────────────────
    log.info("[SAT] Cargando índices desde BD...")
    dfs_indices = cargar_indices_desde_bd(fecha_inicio, fecha_fin)
    log.info("    → EVI: %d fechas, %d parcelas",
             dfs_indices['EVI'].shape[0], dfs_indices['EVI'].shape[1])
    log.info("    → LSWI: %d fechas, %d parcelas",
             dfs_indices['LSWI'].shape[0], dfs_indices['LSWI'].shape[1])

    # ── 3. Cargar datos climáticos desde BD ──────────────────────────────
    log.info("[CLIMA] Cargando datos climáticos desde BD...")
    dfs_clima = cargar_clima_desde_bd(fecha_inicio, fecha_fin)
    log.info("    → Temperatura: %d fechas", dfs_clima['temperature-mean'].shape[0])
    log.info("    → Radiación: %d fechas", dfs_clima['solar-radiation-flux'].shape[0])

    # ── 4. Climatología ──────────────────────────────────────────────────
    log.info("[CHART] Calculando y persistiendo climatología...")
    df_temp = dfs_clima["temperature-mean"]
    df_ssrd = dfs_clima["solar-radiation-flux"]

    serie_temp = df_temp.iloc[:, 0].dropna()
    serie_rad = df_ssrd.iloc[:, 0].dropna()

    clima_temp = construir_climatologia_diaria(serie_temp)
    clima_rad = construir_climatologia_diaria(serie_rad)

    anio_min = serie_temp.index.year.min()
    anio_max = serie_temp.index.year.max()

    guardar_climatologia_diaria(clima_rad, clima_temp, anio_min, anio_max)
    log.info("    → Climatología persistida (años %d-%d).", anio_min, anio_max)

    # ── 5. Suavizado Whittaker sobre EVI ─────────────────────────────────
    log.info("[GEAR] Suavizando EVI con Whittaker-Eilers...")
    df_evi = dfs_indices["EVI"].copy()

    df_evi = df_evi.mask((df_evi < -1.0) | (df_evi > 1.0), np.nan)

    rango_diario = pd.date_range(
        start=df_evi.index.min(),
        end=df_evi.index.max(),
        freq="D",
    )
    df_evi = df_evi.reindex(rango_diario)

    dfs_suavizado = aplicar_whittaker_series(
        {"EVI": df_evi},
        lambda_param=lambda_param,
    )
    df_evi = dfs_suavizado["EVI"]
    log.info("    → EVI suavizado: %d días, %d parcelas",
             df_evi.shape[0], df_evi.shape[1])

    # ── 6. Segmentación de ciclos por parcela ────────────────────────────
    log.info("[SEED] Segmentando ciclos fenológicos por parcela...")
    segmentos_por_parcela: dict[int, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}

    for col in df_evi.columns:
        try:
            id_parcela = int(col.split("_")[1])
        except (IndexError, ValueError):
            continue

        serie = df_evi[col].dropna()
        if serie.empty:
            continue

        segmentos = segmentar_ciclos(
            serie=serie,
            distancia_min_dias=distancia_min_dias,
            prominencia_min=prominencia_min,
        )
        segmentos_por_parcela[id_parcela] = segmentos

    n_con_segmentos = len(segmentos_por_parcela)
    total_segmentos = sum(len(v) for v in segmentos_por_parcela.values())
    log.info("    → %d parcela(s) con segmentos detectados (%d ciclos en total).",
             n_con_segmentos, total_segmentos)
    for idp, segs in segmentos_por_parcela.items():
        log.debug("    segmentos parcela %d: %s", idp,
                  [(str(s[0].date()), str(s[1].date())) for s in segs])

    # ── 7. LSWI máximo histórico ─────────────────────────────────────────
    log.info("[WAVE] Calculando LSWI máximo desde TODOS los segmentos...")
    seed_lswi_max(
        dfs_indices["LSWI"],
        segmentos_por_parcela,
        lambda_param=lambda_param,
    )

    # ── 8. Detección de SOS por segmento y persistencia ───────────────────
    log.info("[SEED] Detectando SOS en cada segmento...")
    sos_por_segmento: dict[int, list[dict]] = {}
    sos_detectados = 0
    ciclos_creados = 0
    ciclos_creados_info: list[dict] = []

    for id_parcela, segmentos in segmentos_por_parcela.items():
        col = f"id_{id_parcela}"
        if col not in df_evi.columns:
            continue

        lista_resultados = []
        for inicio, fin in segmentos:
            serie_seg = df_evi.loc[inicio:fin, col].dropna()
            if serie_seg.empty:
                continue

            resultado = detectar_sos(
                serie=serie_seg.values,
                fechas=serie_seg.index,
                factor=factor_sos,
                ventana_busqueda=(inicio, fin),
            )
            resultado["inicio_segmento"] = inicio
            resultado["fin_segmento"] = fin
            lista_resultados.append(resultado)

            sos_fecha = resultado.get("sos_fecha")
            if sos_fecha is not None:
                sos_detectados += 1
                id_ciclo = crear_ciclo_historico(
                    id_parcela=id_parcela,
                    sos_fecha=sos_fecha,
                    eos_fecha=fin,
                    fecha_inicio=inicio,
                    fecha_fin=fin,
                )
                ciclos_creados += 1
                ciclos_creados_info.append({
                    "id_ciclo": id_ciclo,
                    "id_parcela": id_parcela,
                    "sos_fecha": sos_fecha,
                    "eos_fecha": fin,
                    "segmento_inicio": inicio,
                    "segmento_fin": fin,
                })
                log.info("    [CICLO] parcela=%d id_ciclo=%d sos=%s eos=%s",
                         id_parcela, id_ciclo, sos_fecha.date(), fin.date())

        if lista_resultados:
            sos_por_segmento[id_parcela] = lista_resultados

    log.info("    → SOS detectado en %d/%d segmentos (%d parcelas).",
             sos_detectados, total_segmentos, len(sos_por_segmento))
    log.info("    → %d ciclo(s) histórico(s) creado(s) en BD.", ciclos_creados)

    # ── 9. Predicciones por ventana para cada ciclo histórico ─────────────
    if ciclos_creados_info:
        from datetime import date as _date
        from config import VENTANAS, DIAS_VENTANAS as _DV
        from pipeline.flujos_trabajo import ejecutar_prediccion_ventana

        log.info("[PRED] Generando predicciones para %d ciclo(s)...", len(ciclos_creados_info))
        predicciones_ok = 0
        for cinfo in ciclos_creados_info:
            for ventana in VENTANAS:
                if ventana == "EOS":
                    fecha_ventana = cinfo["eos_fecha"]
                else:
                    fecha_ventana = cinfo["sos_fecha"] + pd.Timedelta(days=_DV[ventana])
                if fecha_ventana.date() > _date.today():
                    continue
                log.debug("    [PRED] ciclo=%d parcela=%d ventana=%s fecha_ventana=%s eos=%s",
                          cinfo["id_ciclo"], cinfo["id_parcela"], ventana,
                          fecha_ventana.date(), cinfo["eos_fecha"].date())
                res = ejecutar_prediccion_ventana(
                    id_ciclo=cinfo["id_ciclo"],
                    ventana=ventana,
                    fecha_hoy=fecha_ventana.date(),
                    lambda_param=lambda_param,
                )
                if res is not None:
                    predicciones_ok += 1
                    log.info("      [OK] yield_qq_ha=%.1f gpp_acum=%.1f",
                             res.get("yield_qq_ha", 0), res.get("gpp_acumulado", 0))
                else:
                    log.warning("      [FAIL] prediccion_ventana retornó None")
        log.info("    → %d prediccion(es) generada(s) exitosamente.", predicciones_ok)
    else:
        log.info("[PRED] Sin ciclos creados, se omite generación de predicciones.")

    return {
        "indices_crudos": dfs_indices,
        "clima": dfs_clima,
        "evi_suavizado": df_evi,
        "segmentos_por_parcela": segmentos_por_parcela,
        "sos_por_segmento": sos_por_segmento,
    }
