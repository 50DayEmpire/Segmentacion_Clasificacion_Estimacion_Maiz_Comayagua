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
from datetime import date, datetime

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
)
from pipeline.ingesta import obtener_indices, obtener_clima
from contextlib import closing

from utils.aplicar_whittaker import aplicar_whittaker_series
from utils.conexionDB import get_connection_raw
from pipeline.modulo_fenologico import segmentar_ciclos, detectar_sos, crear_ciclo_historico
from pipeline.modulo_predictivo import construir_climatologia_diaria, guardar_climatologia_diaria


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
    Calcula el LSWI máximo por parcela a partir de los primeros 2 segmentos
    (ciclos) detectados en la serie y persiste en ``lswi_maximo``:
    el valor más alto de ambos como temporada ``primera`` y el otro como
    ``postrera``.
    Cada segmento se suaviza de forma independiente con Whittaker.

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
    filas = []
    for id_parcela, segmentos in segmentos_por_parcela.items():
        col = f"id_{id_parcela}"
        if col not in df_lswi_crudo.columns:
            continue

        primeros_2 = segmentos[:2]
        if not primeros_2:
            continue

        maximos_por_segmento = []
        for inicio, fin in primeros_2:
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
            maximos_por_segmento.append(suave[col].max())

        if len(maximos_por_segmento) < 1:
            continue

        maximos_por_segmento.sort(reverse=True)
        primera = maximos_por_segmento[0]
        postrera = maximos_por_segmento[1] if len(maximos_por_segmento) > 1 else None

        filas.append((id_parcela, float(primera), "primera"))
        if postrera is not None:
            filas.append((id_parcela, float(postrera), "postrera"))

    if not filas:
        print("    [WARN]   No se pudieron calcular valores de LSWI máximo.")
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

    print(f"    → LSWI máximo persistido para {len(filas)} parcela(s)-temporada(s).")
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
    distancia_min_dias: int = 90,
    prominencia_min: float = 0.15,
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
        Separación mínima entre valles para segmentación (default 90).
    prominencia_min : float, opcional
        Profundidad mínima del valle para segmentación (default 0.15).

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

    # ── 3. Ingesta de índices Sentinel-2 ───────────────────────────────────
    print(f"\n[SAT]   Ingestando índices EVI/LSWI desde {fecha_inicio}...")
    dfs_indices = obtener_indices(
        connection=conn_cdse,
        geojson_openeo=geojson,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )
    print(f"    → EVI: {dfs_indices['EVI'].shape[0]} fechas, "
          f"{dfs_indices['EVI'].shape[1]} parcelas")
    print(f"    → LSWI: {dfs_indices['LSWI'].shape[0]} fechas, "
          f"{dfs_indices['LSWI'].shape[1]} parcelas")

    # ── 4. Ingesta de datos climáticos ─────────────────────────────────────
    print(f"\n[TEMP]   Ingestando datos climáticos (AgERA5) desde {fecha_inicio}...")
    dfs_clima = obtener_clima(
        connection=conn_fed,
        geojson_openeo=geojson,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        num_parc=n_parcelas,
    )
    print(f"    → Temperatura: {dfs_clima['temperature-mean'].shape[0]} fechas")
    print(f"    → Radiación: {dfs_clima['solar-radiation-flux'].shape[0]} fechas")

    # ── 5. Climatología ──────────────────────────────────────────────────
    print(f"\n[CHART]  Calculando y persistiendo climatología (PAR + temperatura)...")

    df_temp = dfs_clima["temperature-mean"]
    df_ssrd = dfs_clima["solar-radiation-flux"]

    serie_temp = df_temp.iloc[:, 0].dropna()
    serie_par = (df_ssrd.iloc[:, 0] / 1e6 * 0.45).dropna()

    clima_temp = construir_climatologia_diaria(serie_temp)
    clima_par = construir_climatologia_diaria(serie_par)

    anio_min = serie_temp.index.year.min()
    anio_max = serie_temp.index.year.max()

    guardar_climatologia_diaria(clima_par, clima_temp, anio_min, anio_max)
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

    # ── 9. LSWI máximo histórico ──────────────────────────────────────────
    print(f"\n[WAVE]  Calculando LSWI máximo desde los primeros 2 segmentos...")
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
                crear_ciclo_historico(
                    id_parcela=id_parcela,
                    sos_fecha=sos_fecha,
                )
                ciclos_creados += 1

        if lista_resultados:
            sos_por_segmento[id_parcela] = lista_resultados

    print(f"    → SOS detectado en {sos_detectados}/{total_segmentos} segmentos "
          f"({len(sos_por_segmento)} parcelas).")
    print(f"    → {ciclos_creados} ciclo(s) histórico(s) creado(s) en BD.")

    # ── Resultado ──────────────────────────────────────────────────────────
    return {
        "indices_crudos": dfs_indices,
        "clima": dfs_clima,
        "evi_suavizado": df_evi,
        "segmentos_por_parcela": segmentos_por_parcela,
        "sos_por_segmento": sos_por_segmento,
    }
