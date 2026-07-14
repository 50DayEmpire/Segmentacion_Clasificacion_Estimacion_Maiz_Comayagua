# pipeline/flujos_trabajo.py — Orquestador de Predicción de Rendimiento
"""
Motor de predicción de rendimiento de maíz para el Valle de Comayagua.

Define cuatro flujos de trabajo que conectan, en orden, todas las
funciones reutilizables del pipeline: ingesta con caché,
preprocesamiento VPM, detección fenológica de inicio de temporada (SOS),
cálculo de GPP y estimación de rendimiento final.

Flujos disponibles
------------------
1. ``ejecutar_pipeline_completo``
   Flujo end-to-end con descarga inteligente: consulta BD primero y solo
   llama a openEO para los rangos de fechas que faltan (gaps).  Requiere
   conexiones openEO activas, que solo se usan si hay gaps.

2. ``ejecutar_pipeline_desde_bd``
   Flujo sin openEO: carga índices y clima directamente desde la BD.
   Falla con ValueError si el rango pedido no está en BD.  Ideal para
   re-procesar un ciclo ya ingestado sin coste de red.

3. ``calcular_rendimiento_desde_indices``
   Núcleo de procesamiento puro: recibe los DataFrames ya en memoria y
   ejecuta preprocesamiento → GPP → fenología → rendimiento.  Útil en
   notebooks o cuando la ingesta se hizo en otro paso.

4. ``ejecutar_prediccion_ventana``
   Orquesta la predicción para una ventana T1/T2/T3 de un ciclo
   histórico.  Carga índices crudos desde BD, preprocesa, persiste
   índices suavizados y delega la extensión sintética + VPM a
   ``modulo_predictivo.ejecutar_prediccion_ventana``.

Uso típico
----------
    # Flujo 1 — conexión openEO solo si hay gaps en BD
    from pipeline.flujos_trabajo import ejecutar_pipeline_completo
    resultados = ejecutar_pipeline_completo(
        connection=conn_cdse,
        connection_fed=conn_fed,
        geojson_openeo=geojson,
        fecha_inicio="2025-05-01",
        fecha_fin="2025-10-30",
    )

    # Flujo 2 — completamente desde BD
    from pipeline.flujos_trabajo import ejecutar_pipeline_desde_bd
    resultados = ejecutar_pipeline_desde_bd(
        fecha_inicio="2025-05-01",
        fecha_fin="2025-10-30",
    )

    # Flujo 3 — DataFrames ya en memoria
    from pipeline.flujos_trabajo import calcular_rendimiento_desde_indices
    resultados = calcular_rendimiento_desde_indices(
        dfs_crudos=dfs_crudos,
        dfs_clima=dfs_clima,
        fecha_fin="2025-10-30",
    )
"""
from __future__ import annotations

from datetime import date

import openeo
import pandas as pd

from pipeline.ingesta import (
    obtener_indices,
    obtener_clima,
    cargar_indices_desde_bd,
    cargar_clima_desde_bd,
)
from pipeline.modulo_vpm import (
    preprocesar_indices_vpm,
    calcular_gpp_vpm,
    calcular_biomasa_y_rendimiento,
)
from pipeline.modulo_fenologico import detectar_sos
from pipeline.modulo_vpm import guardar_indices_suavizados
from pipeline.modulo_predictivo import (
    ejecutar_prediccion_ventana as _ejecutar_prediccion_ventana_core,
    existe_prediccion_ventana,
)
from config import DIAS_VENTANAS

# ── Defaults del modelo VPM ───────────────────────────────────────────────────
_VPM_DEFAULTS: dict = {
    "epsilon_0":    1.6,
    "t_min":        10.0,
    "t_opt":        30.0,
    "t_max":        45.0,
    "par_fraction": 0.48,
}

# ── Defaults de conversión a rendimiento ─────────────────────────────────────
_RENDIMIENTO_DEFAULTS: dict = {
    "cue":               0.55,
    "fraccion_carbono":  0.45,
    "harvest_index":     0.48,
}


# =============================================================================
# FUNCIÓN PRIVADA — núcleo fenológico compartido por los tres flujos
# =============================================================================

def _calcular_fenologia_y_rendimiento(
    dfs_vpm: dict[str, pd.DataFrame],
    dfs_gpp: dict[str, pd.DataFrame],
    fecha_inicio: str,
    fecha_fin: str,
    ventana_sos: tuple[str, str] | None,
    config_rendimiento: dict | None,
) -> dict:
    """
    Detecta el SOS por parcela, recorta el GPP al período vegetativo de cada
    parcela de forma diferenciada y estima biomasa y rendimiento final.

    Parámetros
    ----------
    dfs_vpm : dict[str, pd.DataFrame]
        Salida de ``preprocesar_indices_vpm``. Debe contener la clave "EVI".
    dfs_gpp : dict[str, pd.DataFrame]
        Salida de ``calcular_gpp_vpm``. Debe contener la clave "GPP".
    fecha_inicio : str
        Fecha de inicio del ciclo ("YYYY-MM-DD"). Fallback de SOS si no se
        detecta inicio de temporada para una parcela concreta.
    fecha_fin : str
        Fecha de fin del período de evaluación ("YYYY-MM-DD").
    ventana_sos : tuple(str, str) | None
        Ventana de búsqueda de SOS. Si None, se busca en toda la serie.
    config_rendimiento : dict | None
        Overrides para ``calcular_biomasa_y_rendimiento``.
        Claves: "cue", "fraccion_carbono", "harvest_index".

    Retorna
    -------
    dict con:
        - "fenologia"  : pd.DataFrame — tabla SOS/POS por parcela.
        - "rendimiento": dict — salida de ``calcular_biomasa_y_rendimiento``.
    """
    cfg_rend        = {**_RENDIMIENTO_DEFAULTS, **(config_rendimiento or {})}
    df_evi          = dfs_vpm["EVI"]
    df_gpp          = dfs_gpp["GPP"]
    fecha_inicio_ts = pd.Timestamp(fecha_inicio)
    fecha_fin_ts    = pd.Timestamp(fecha_fin)

    # ── 1. Detectar SOS por parcela ───────────────────────────────────────────
    print("\n🌱 Detectando inicio de temporada (SOS) por parcela...")
    registros_fenologia = []
    sos_por_parcela: dict[str, pd.Timestamp] = {}

    for parcela in df_evi.columns:
        info      = detectar_sos(
            serie=df_evi[parcela].values,
            fechas=df_evi.index,
            ventana_busqueda=ventana_sos,
        )
        sos_fecha = info["sos_fecha"]

        if sos_fecha is None:
            print(
                f"   ⚠️  No se detectó SOS para '{parcela}'. "
                f"Se usa fecha_inicio ({fecha_inicio}) como fallback."
            )
            sos_fecha = fecha_inicio_ts

        sos_por_parcela[parcela] = pd.Timestamp(sos_fecha)
        registros_fenologia.append({
            "parcela":    parcela,
            "sos_fecha":  sos_fecha,
            "sos_valor":  info["sos_valor"],
            "pos_fecha":  info["pos_fecha"],
            "pos_valor":  info["pos_valor"],
            "base_valor": info["base_valor"],
            "amplitud":   info["amplitud"],
            "umbral":     info["umbral"],
        })

    tabla_sos = pd.DataFrame(registros_fenologia).set_index("parcela")
    print(f"   ✔️ SOS detectado para {tabla_sos['sos_fecha'].notna().sum()} / {len(tabla_sos)} parcelas.")
    print(tabla_sos)

    # ── 2. Recortar GPP al período vegetativo diferenciado por parcela ────────
    print("\n✂️  Recortando GPP al período vegetativo de cada parcela...")
    gpp_recortado_por_parcela: dict[str, pd.Series] = {}

    for parcela in df_gpp.columns:
        sos     = sos_por_parcela[parcela]
        mascara = (df_gpp.index >= sos) & (df_gpp.index <= fecha_fin_ts)
        gpp_recortado_por_parcela[parcela] = df_gpp.loc[mascara, parcela]

    df_gpp_recortado = (
        pd.DataFrame(gpp_recortado_por_parcela)
        .fillna(0.0)
        .sort_index()
    )

    # ── 3. Estimar biomasa y rendimiento ──────────────────────────────────────
    print("\n🌽 Estimando biomasa y rendimiento final...")
    resultado_rendimiento = calcular_biomasa_y_rendimiento(
        df_gpp_recortado=df_gpp_recortado,
        cue=cfg_rend["cue"],
        fraccion_carbono=cfg_rend["fraccion_carbono"],
        harvest_index=cfg_rend["harvest_index"],
    )

    return {
        "fenologia":   tabla_sos,
        "rendimiento": resultado_rendimiento,
    }


def _imprimir_resumen(label: str, yield_tha: pd.Series) -> None:
    yield_mean = yield_tha.mean()
    print("\n" + "=" * 60)
    print(f"✅ {label}")
    print(f"   ✔️ Parcelas evaluadas   : {len(yield_tha)}")
    print(f"   ✔️ Rendimiento promedio : {yield_mean:.3f} t/ha")
    print(f"   ✔️ Rendimiento promedio : {yield_mean * 22.0458:.1f} qq/ha")
    print("=" * 60)


# =============================================================================
# FLUJO 1 — Pipeline completo con descarga inteligente (caché BD + gaps openEO)
# =============================================================================

def ejecutar_pipeline_completo(
    connection: openeo.Connection,
    geojson_openeo: dict,
    fecha_inicio: str,
    fecha_fin: str,
    connection_fed: openeo.Connection | None = None,
    config_cloud_mask: dict | None = None,
    lambda_param: float = 4000.0,
    lswi_max: pd.Series | dict[str, float] | None = None,
    config_vpm: dict | None = None,
    config_rendimiento: dict | None = None,
    ventana_sos: tuple[str, str] | None = None,
) -> dict:
    """
    Flujo end-to-end con descarga inteligente: consulta BD primero y solo
    llama a openEO para los sub-rangos de fechas que no están en BD.
    Si BD ya tiene cobertura completa, las conexiones openEO no se usan.

    Parámetros
    ----------
    connection : openeo.Connection
        Conexión al backend **CDSE**. Solo se usa si hay gaps de índices.
    connection_fed : openeo.Connection | None
        Conexión al backend **federado** (AgERA5). Solo se usa si hay gaps
        climáticos. Si es None, se reutiliza ``connection`` con advertencia.
    geojson_openeo : dict
        GeoJSON FeatureCollection con las parcelas en EPSG:4326.
    fecha_inicio : str
        Inicio del ciclo en formato "YYYY-MM-DD".
    fecha_fin : str
        Fin del ciclo en formato "YYYY-MM-DD".
    config_cloud_mask : dict | None
        Overrides para la máscara SCL de Sentinel-2.
        Claves: kernel1_size, kernel2_size, mask1_values,
        mask2_values, erosion_kernel_size.
    lambda_param : float
        Parámetro de suavizado Whittaker-Eilers (por defecto 4000.0).
    lswi_max : pd.Series | dict[str, float] | None
        LSWI máximo histórico por parcela. Si None se calcula desde la serie.
    config_vpm : dict | None
        Overrides del modelo VPM.
        Claves: epsilon_0, t_min, t_opt, t_max, par_fraction.
    config_rendimiento : dict | None
        Overrides de conversión a rendimiento.
        Claves: cue, fraccion_carbono, harvest_index.
    ventana_sos : tuple(str, str) | None
        Ventana de búsqueda de SOS como ("YYYY-MM-DD", "YYYY-MM-DD").
        Si None se busca en toda la serie.

    Retorna
    -------
    dict con:
        - "indices_crudos" : dict[str, pd.DataFrame]
        - "clima"          : dict[str, pd.DataFrame]
        - "vegetacion"     : dict[str, pd.DataFrame]
        - "gpp"            : dict[str, pd.DataFrame]
        - "fenologia"      : pd.DataFrame
        - "rendimiento"    : dict
    """
    cfg_vpm    = {**_VPM_DEFAULTS, **(config_vpm or {})}
    conn_clima = connection_fed if connection_fed is not None else connection

    if connection_fed is None:
        import warnings
        warnings.warn(
            "connection_fed no fue proporcionado. Se usará 'connection' para AgERA5, "
            "lo cual puede fallar si el backend CDSE no expone esa colección.",
            stacklevel=2,
        )

    print("=" * 60)
    print("🚀 MOTOR DE PREDICCIÓN VPM — PIPELINE COMPLETO (BD + openEO)")
    print(f"   Período: {fecha_inicio}  →  {fecha_fin}")
    print("=" * 60)

    # ── Paso 1: Índices con caché ─────────────────────────────────────────────
    print("\n📡 PASO 1/5 — Índices EVI/LSWI (BD primero, openEO para gaps)...")
    dfs_crudos = obtener_indices(
        connection=connection,
        geojson_openeo=geojson_openeo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        config_cloud_mask=config_cloud_mask,
    )

    # ── Paso 2: Clima con caché ───────────────────────────────────────────────
    print("\n🌤️  PASO 2/5 — Clima AgERA5 (BD primero, openEO para gaps)...")
    dfs_clima = obtener_clima(
        connection=conn_clima,
        geojson_openeo=geojson_openeo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )

    # ── Paso 3: Preprocesamiento VPM ──────────────────────────────────────────
    print("\n🔬 PASO 3/5 — Preprocesamiento de índices VPM...")
    dfs_vpm = preprocesar_indices_vpm(
        dfs_vpm_crudos=dfs_crudos,
        lambda_param=lambda_param,
        lswi_max=lswi_max,
    )

    # ── Paso 4: GPP ───────────────────────────────────────────────────────────
    print("\n⚡ PASO 4/5 — GPP diario (modelo VPM)...")
    dfs_gpp = calcular_gpp_vpm(
        dfs_vegetacion=dfs_vpm,
        dfs_clima=dfs_clima,
        epsilon_0=cfg_vpm["epsilon_0"],
        t_min=cfg_vpm["t_min"],
        t_opt=cfg_vpm["t_opt"],
        t_max=cfg_vpm["t_max"],
        par_fraction=cfg_vpm["par_fraction"],
    )

    # ── Paso 5: Fenología y rendimiento ───────────────────────────────────────
    print("\n🌽 PASO 5/5 — SOS y estimación de rendimiento...")
    resultado = _calcular_fenologia_y_rendimiento(
        dfs_vpm=dfs_vpm,
        dfs_gpp=dfs_gpp,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        ventana_sos=ventana_sos,
        config_rendimiento=config_rendimiento,
    )

    _imprimir_resumen("PIPELINE COMPLETO FINALIZADO", resultado["rendimiento"]["yield_final_tha"])

    return {
        "indices_crudos": dfs_crudos,
        "clima":          dfs_clima,
        "vegetacion":     dfs_vpm,
        "gpp":            dfs_gpp,
        "fenologia":      resultado["fenologia"],
        "rendimiento":    resultado["rendimiento"],
    }


# =============================================================================
# FLUJO 2 — Pipeline completamente desde BD (sin conexión openEO)
# =============================================================================

def ejecutar_pipeline_desde_bd(
    fecha_inicio: str,
    fecha_fin: str,
    ids_parcelas: list[int] | None = None,
    lambda_param: float = 4000.0,
    lswi_max: pd.Series | dict[str, float] | None = None,
    config_vpm: dict | None = None,
    config_rendimiento: dict | None = None,
    ventana_sos: tuple[str, str] | None = None,
) -> dict:
    """
    Flujo de procesamiento sin conexión openEO: lee índices y clima
    directamente desde ``series_diarias_vpm`` y ejecuta el pipeline completo.

    Útil para re-procesar un ciclo ya ingestado sin coste de red, cambiar
    parámetros del modelo o correr predicciones en lote.

    Parámetros
    ----------
    fecha_inicio : str
        Inicio del ciclo en formato "YYYY-MM-DD".
    fecha_fin : str
        Fin del ciclo en formato "YYYY-MM-DD".
    ids_parcelas : list[int] | None
        Subconjunto de parcelas a procesar. Si None, se usan todas.
    lambda_param : float
        Parámetro de suavizado Whittaker-Eilers (por defecto 4000.0).
    lswi_max : pd.Series | dict[str, float] | None
        LSWI máximo histórico por parcela. Si None se calcula desde la serie.
    config_vpm : dict | None
        Overrides del modelo VPM.
        Claves: epsilon_0, t_min, t_opt, t_max, par_fraction.
    config_rendimiento : dict | None
        Overrides de conversión a rendimiento.
        Claves: cue, fraccion_carbono, harvest_index.
    ventana_sos : tuple(str, str) | None
        Ventana de búsqueda de SOS. Si None se busca en toda la serie.

    Retorna
    -------
    dict con:
        - "indices_crudos" : dict[str, pd.DataFrame]
        - "clima"          : dict[str, pd.DataFrame]
        - "vegetacion"     : dict[str, pd.DataFrame]
        - "gpp"            : dict[str, pd.DataFrame]
        - "fenologia"      : pd.DataFrame
        - "rendimiento"    : dict

    Raises
    ------
    ValueError
        Si el rango solicitado no está cubierto en BD para índices o clima.
    """
    cfg_vpm = {**_VPM_DEFAULTS, **(config_vpm or {})}

    print("=" * 60)
    print("🗄️  MOTOR DE PREDICCIÓN VPM — PIPELINE DESDE BD")
    print(f"   Período: {fecha_inicio}  →  {fecha_fin}")
    print("=" * 60)

    # ── Paso 1: Cargar índices ────────────────────────────────────────────────
    print("\n📂 PASO 1/5 — Cargando índices EVI/LSWI desde BD...")
    dfs_crudos = cargar_indices_desde_bd(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        ids_parcelas=ids_parcelas,
    )

    # ── Paso 2: Cargar clima ──────────────────────────────────────────────────
    print("\n📂 PASO 2/5 — Cargando datos climáticos desde BD...")
    dfs_clima = cargar_clima_desde_bd(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        ids_parcelas=ids_parcelas,
    )

    # ── Paso 3: Preprocesamiento VPM ──────────────────────────────────────────
    print("\n🔬 PASO 3/5 — Preprocesamiento de índices VPM...")
    dfs_vpm = preprocesar_indices_vpm(
        dfs_vpm_crudos=dfs_crudos,
        lambda_param=lambda_param,
        lswi_max=lswi_max,
    )

    # ── Paso 4: GPP ───────────────────────────────────────────────────────────
    print("\n⚡ PASO 4/5 — GPP diario (modelo VPM)...")
    dfs_gpp = calcular_gpp_vpm(
        dfs_vegetacion=dfs_vpm,
        dfs_clima=dfs_clima,
        epsilon_0=cfg_vpm["epsilon_0"],
        t_min=cfg_vpm["t_min"],
        t_opt=cfg_vpm["t_opt"],
        t_max=cfg_vpm["t_max"],
        par_fraction=cfg_vpm["par_fraction"],
    )

    # ── Paso 5: Fenología y rendimiento ───────────────────────────────────────
    print("\n🌽 PASO 5/5 — SOS y estimación de rendimiento...")
    resultado = _calcular_fenologia_y_rendimiento(
        dfs_vpm=dfs_vpm,
        dfs_gpp=dfs_gpp,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        ventana_sos=ventana_sos,
        config_rendimiento=config_rendimiento,
    )

    _imprimir_resumen("PIPELINE DESDE BD FINALIZADO", resultado["rendimiento"]["yield_final_tha"])

    return {
        "indices_crudos": dfs_crudos,
        "clima":          dfs_clima,
        "vegetacion":     dfs_vpm,
        "gpp":            dfs_gpp,
        "fenologia":      resultado["fenologia"],
        "rendimiento":    resultado["rendimiento"],
    }


# =============================================================================
# FLUJO 3 — Núcleo de procesamiento puro (DataFrames ya en memoria)
# =============================================================================

def calcular_rendimiento_desde_indices(
    dfs_crudos: dict[str, pd.DataFrame],
    dfs_clima: dict[str, pd.DataFrame],
    fecha_fin: str,
    fecha_inicio: str | None = None,
    lambda_param: float = 4000.0,
    lswi_max: pd.Series | dict[str, float] | None = None,
    config_vpm: dict | None = None,
    config_rendimiento: dict | None = None,
    ventana_sos: tuple[str, str] | None = None,
) -> dict:
    """
    Núcleo de procesamiento puro: recibe los DataFrames ya en memoria y
    ejecuta preprocesamiento → GPP → fenología → rendimiento.

    Útil en notebooks, pruebas o cuando la ingesta se hizo en un paso
    anterior y los datos ya están disponibles como variables Python.

    Parámetros
    ----------
    dfs_crudos : dict[str, pd.DataFrame]
        Índices EVI y LSWI crudos. Mismo esquema que el resultado de
        ``obtener_indices`` / ``cargar_indices_desde_bd``.
    dfs_clima : dict[str, pd.DataFrame]
        Datos climáticos. Mismo esquema que ``obtener_clima`` /
        ``cargar_clima_desde_bd``.
    fecha_fin : str
        Fin del período de evaluación en formato "YYYY-MM-DD".
    fecha_inicio : str | None
        Inicio del ciclo. Si None se infiere del índice mínimo del EVI crudo.
        Se usa como fallback de SOS para parcelas sin detección.
    lambda_param : float
        Parámetro de suavizado Whittaker-Eilers (por defecto 4000.0).
    lswi_max : pd.Series | dict[str, float] | None
        LSWI máximo histórico por parcela. Si None se calcula desde la serie.
    config_vpm : dict | None
        Overrides del modelo VPM.
        Claves: epsilon_0, t_min, t_opt, t_max, par_fraction.
    config_rendimiento : dict | None
        Overrides de conversión a rendimiento.
        Claves: cue, fraccion_carbono, harvest_index.
    ventana_sos : tuple(str, str) | None
        Ventana de búsqueda de SOS. Si None se busca en toda la serie.

    Retorna
    -------
    dict con:
        - "vegetacion"  : dict[str, pd.DataFrame]
        - "gpp"         : dict[str, pd.DataFrame]
        - "fenologia"   : pd.DataFrame
        - "rendimiento" : dict
    """
    cfg_vpm = {**_VPM_DEFAULTS, **(config_vpm or {})}

    if fecha_inicio is None:
        fecha_inicio = str(dfs_crudos["EVI"].index.min().date())
        print(f"ℹ️  fecha_inicio no especificada. Se infiere: {fecha_inicio}")

    print("=" * 60)
    print("🔁 MOTOR DE PREDICCIÓN VPM — NÚCLEO DESDE MEMORIA")
    print(f"   Período: {fecha_inicio}  →  {fecha_fin}")
    print("=" * 60)

    # ── Paso 1: Preprocesamiento VPM ──────────────────────────────────────────
    print("\n🔬 PASO 1/3 — Preprocesamiento de índices VPM...")
    dfs_vpm = preprocesar_indices_vpm(
        dfs_vpm_crudos=dfs_crudos,
        lambda_param=lambda_param,
        lswi_max=lswi_max,
    )

    # ── Paso 2: GPP ───────────────────────────────────────────────────────────
    print("\n⚡ PASO 2/3 — GPP diario (modelo VPM)...")
    dfs_gpp = calcular_gpp_vpm(
        dfs_vegetacion=dfs_vpm,
        dfs_clima=dfs_clima,
        epsilon_0=cfg_vpm["epsilon_0"],
        t_min=cfg_vpm["t_min"],
        t_opt=cfg_vpm["t_opt"],
        t_max=cfg_vpm["t_max"],
        par_fraction=cfg_vpm["par_fraction"],
    )

    # ── Paso 3: Fenología y rendimiento ───────────────────────────────────────
    print("\n🌽 PASO 3/3 — SOS y estimación de rendimiento...")
    resultado = _calcular_fenologia_y_rendimiento(
        dfs_vpm=dfs_vpm,
        dfs_gpp=dfs_gpp,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        ventana_sos=ventana_sos,
        config_rendimiento=config_rendimiento,
    )

    _imprimir_resumen("NÚCLEO COMPLETADO", resultado["rendimiento"]["yield_final_tha"])

    return {
        "vegetacion":  dfs_vpm,
        "gpp":         dfs_gpp,
        "fenologia":   resultado["fenologia"],
        "rendimiento": resultado["rendimiento"],
    }


# =============================================================================
# FLUJO 4 — Predicción por ventana para un ciclo (id_ciclo + T1/T2/T3)
# =============================================================================
from contextlib import closing
from utils.conexionDB import get_connection_raw
def _cargar_ciclo(id_ciclo: int) -> dict | None:
    sql = """
        SELECT id_ciclo, id_parcela, temporada, lswi_max,
               sos, t1, t2, t3, eos, estado_ciclo,
               fecha_inicio, fecha_fin
        FROM produccion_acumulada_ciclo
        WHERE id_ciclo = ?
    """
    with closing(get_connection_raw()) as conn:
        row = conn.execute(sql, (id_ciclo,)).fetchone()
    if row is None:
        return None
    cols = [
        "id_ciclo", "id_parcela", "temporada", "lswi_max",
        "sos", "t1", "t2", "t3", "eos", "estado_ciclo",
        "fecha_inicio", "fecha_fin",
    ]
    return dict(zip(cols, row))


def _obtener_lswi_max_historico(id_parcela: int, temporada: str) -> float | None:
    sql = """
        SELECT lswi_max FROM lswi_maximo
        WHERE id_parcela = ? AND temporada = ?
    """
    with closing(get_connection_raw()) as conn:
        row = conn.execute(sql, (id_parcela, temporada)).fetchone()
    return float(row[0]) if row else None


def ejecutar_prediccion_ventana(
    id_ciclo: int,
    ventana: str,
    fecha_hoy: date | None = None,
    lambda_param: float = 4000.0,
) -> dict | None:
    """
    Orquesta la predicción de rendimiento para una ventana T1/T2/T3
    de un ciclo almacenado en ``produccion_acumulada_ciclo``.

    Flujo
    -----
    1. Carga el ciclo desde BD.
    2. Calcula ``fecha_ventana`` = SOS + ``DIAS_VENTANAS[ventana]``.
    3. Carga índices crudos EVI/LSWI desde ``series_diarias_vpm``
       entre SOS y ``fecha_ventana``.
    4. Preprocesa (reindexado diario, Whittaker, FPAR, W_scalar).
    5. Persiste índices suavizados en ``indices_suavizados``.
    6. Delega a ``modulo_predictivo.ejecutar_prediccion_ventana``
       la extensión sintética, el cálculo VPM (GPP/NPP/rendimiento)
       y la persistencia en ``predicciones_ventana`` +
       ``series_extrapoladas_ventana``.

    Parámetros
    ----------
    id_ciclo : int
        Identificador del ciclo en ``produccion_acumulada_ciclo``.
    ventana : str
        ``"T1"``, ``"T2"`` o ``"T3"``.
    fecha_hoy : date | None
        Fecha de corte para los datos reales. Por defecto: hoy.
    lambda_param : float
        Parámetro de suavizado Whittaker (por defecto 4000.0).

    Retorna
    -------
    dict | None
        Resultado del motor interno o ``None`` si el ciclo no existe,
        falta SOS, la ventana es inválida, la fecha_ventana es futura,
        o la predicción ya había sido congelada antes.
    """
    # ── 1. Cargar ciclo ──────────────────────────────────────────────────
    ciclo = _cargar_ciclo(id_ciclo)
    if ciclo is None:
        print(f"  [SKIP] Ciclo {id_ciclo} no encontrado en BD.")
        return None

    id_parcela = ciclo["id_parcela"]
    sos_str = ciclo.get("sos")
    if not sos_str:
        print(f"  [SKIP] Ciclo {id_ciclo} (parcela {id_parcela}) sin SOS.")
        return None

    sos_ts = pd.Timestamp(sos_str)
    dias_ventana = DIAS_VENTANAS.get(ventana)
    if dias_ventana is None:
        print(f"  [ERROR] Ventana '{ventana}' no v\u00e1lida (use T1/T2/T3/EOS).")
        return None

    if ventana == "EOS":
        if not ciclo.get("eos"):
            print(f"  [SKIP] Ciclo {id_ciclo} sin fecha EOS real para ventana EOS.")
            return None
        fecha_ventana = pd.Timestamp(ciclo["eos"])
    else:
        fecha_ventana = sos_ts + pd.Timedelta(days=dias_ventana)
    if fecha_hoy is None:
        fecha_hoy = date.today()
    fecha_hoy_ts = pd.Timestamp(fecha_hoy)

    if ventana == "EOS":
        desc = f"EOS real = {fecha_ventana.date()}"
    else:
        desc = f"SOS+{dias_ventana}d = {fecha_ventana.date()}"
    print(f"\n[WFLOW] Predicci\u00f3n ciclo {id_ciclo} | parcela {id_parcela} | "
          f"{ventana} ({desc})")

    if fecha_ventana > fecha_hoy_ts:
        print(f"  [SKIP] fecha_ventana ({fecha_ventana.date()}) > fecha_hoy ({fecha_hoy}).")
        return None

    if ventana != "EOS":
        eos_ciclo_str = ciclo.get("eos")
        if eos_ciclo_str and fecha_ventana > pd.Timestamp(eos_ciclo_str):
            print(f"  [SKIP] {ventana} ({fecha_ventana.date()}) > EOS ({eos_ciclo_str}).")
            return None

    # ── 2. Verificar si ya existe ─────────────────────────────────────────
    if existe_prediccion_ventana(id_ciclo, ventana):
        print(f"  [SKIP] Predicci\u00f3n ya existe para ciclo {id_ciclo}, ventana {ventana}.")
        return None

    # ── 3. Cargar índices crudos desde BD ─────────────────────────────────
    print(f"  [1/5] Cargando EVI/LSWI crudos desde SOS...")
    fecha_fin_lectura = min(fecha_ventana, fecha_hoy_ts)
    try:
        dfs_crudos = cargar_indices_desde_bd(
            fecha_inicio=str(sos_ts.date()),
            fecha_fin=str(fecha_fin_lectura.date()),
            ids_parcelas=[id_parcela],
        )
    except ValueError as e:
        print(f"  [ERROR] {e}")
        return None

    # ── 4. Preprocesar (Whittaker, FPAR, W_scalar) ────────────────────────
    print(f"  [2/5] Preprocesando \u00edndices (Whittaker \u03bb={lambda_param})...")
    dfs_vpm = preprocesar_indices_vpm(
        dfs_vpm_crudos=dfs_crudos,
        lambda_param=lambda_param,
    )

    # ── 5. Persistir índices suavizados ────────────────────────────────────
    print(f"  [3/5] Persistiendo \u00edndices suavizados en `indices_suavizados`...")
    n_suav = guardar_indices_suavizados(id_ciclo, id_parcela, dfs_vpm)
    print(f"        {n_suav} fila(s) escritas.")

    # ── 6. Cargar valor del valle real (fecha_inicio de BD, anterior a SOS) ──
    fecha_inicio_str = str(ciclo.get("fecha_inicio", ""))
    if fecha_inicio_str and fecha_inicio_str != str(sos_ts.date()):
        try:
            dfs_valle = cargar_indices_desde_bd(
                fecha_inicio=fecha_inicio_str,
                fecha_fin=fecha_inicio_str,
                ids_parcelas=[id_parcela],
            )
            col = f"id_{id_parcela}"
            if col in dfs_valle["EVI"].columns:
                serie = dfs_valle["EVI"][col].dropna()
                if not serie.empty:
                    ciclo["valor_valle_evi"] = float(serie.iloc[0])
            if col in dfs_valle["LSWI"].columns:
                serie = dfs_valle["LSWI"][col].dropna()
                if not serie.empty:
                    ciclo["valor_valle_lswi"] = float(serie.iloc[0])
        except Exception:
            pass

    # ── 7. Preparar datos para el motor de predicción ─────────────────────
    print(f"  [4/5] Armando datos para motor VPM...")
    ciclo_ext = dict(ciclo)
    ciclo_ext["fecha_valle"] = str(ciclo.get("fecha_inicio", ""))
    ciclo_ext["fecha_inicio"] = str(sos_ts.date())

    if ciclo_ext.get("lswi_max") is None:
        lswi_max_hist = _obtener_lswi_max_historico(id_parcela, ciclo["temporada"])
        if lswi_max_hist is not None:
            ciclo_ext["lswi_max"] = lswi_max_hist
            print(f"        Usando lswi_max={lswi_max_hist:.3f} de `lswi_maximo` "
                  f"({ciclo['temporada']})")

    dfs_vpm_por_parcela = {id_parcela: dfs_vpm}

    # ── 7. Ejecutar predicción (extensión + GPP + NPP + rendimiento) ──────
    print(f"  [5/5] Ejecutando cadena VPM (GPP \u2192 NPP \u2192 rendimiento)...")
    resultado = _ejecutar_prediccion_ventana_core(
        ciclo=ciclo_ext,
        ventana=ventana,
        fecha_ventana=fecha_ventana.date(),
        dfs_vpm_por_parcela=dfs_vpm_por_parcela,
        fecha_hoy=fecha_hoy,
    )

    if resultado is None:
        print(f"  [ERROR] Predicci\u00f3n fall\u00f3 para ciclo {id_ciclo}, ventana {ventana}.")
    else:
        print(f"  [OK] Predicci\u00f3n completada: "
              f"{resultado.get('yield_qq_ha', 'N/A'):.1f} qq/ha")
        if ventana == "EOS":
            with closing(get_connection_raw()) as conn:
                with conn:
                    conn.execute("""
                        UPDATE produccion_acumulada_ciclo
                        SET rendimiento = ?, produccion_total = ?
                        WHERE id_ciclo = ?
                    """, (
                        resultado.get("yield_qq_ha"),
                        resultado.get("yield_qq_parcela"),
                        id_ciclo,
                    ))
            print(f"  [UPDATE] produccion_acumulada_ciclo: rendimiento="
                  f"{resultado.get('yield_qq_ha', 'N/A'):.1f} qq/ha, "
                  f"produccion_total={resultado.get('yield_qq_parcela', 'N/A'):.1f} qq")

    return resultado


# =============================================================================
# Recalculo en memoria — para ajuste visual de límites SOS/EOS
# =============================================================================

def recalcular_en_memoria(
    id_ciclo: int,
    nuevo_sos: date,
    nuevo_eos: date,
    lambda_param: float = 4000.0,
) -> dict | None:
    """
    Ejecuta predicción EOS con SOS/EOS personalizados SIN persistir nada.
    Usado por la vista de análisis histórico para recalcular producción
    cuando el operador ajusta los límites del ciclo.

    Parámetros
    ----------
    id_ciclo : int
        Identificador del ciclo en ``produccion_acumulada_ciclo``.
    nuevo_sos : date
        Fecha SOS propuesta.
    nuevo_eos : date
        Fecha EOS propuesta.
    lambda_param : float
        Parámetro de suavizado Whittaker (defecto 4000.0).

    Retorna
    -------
    dict | None
        Mismas claves que ``ejecutar_prediccion_ventana`` (yield_qq_ha,
        yield_qq_parcela, gpp_acumulado, etc.) o None si falla.
    """
    from pipeline.ingesta import cargar_indices_desde_bd

    ciclo = _cargar_ciclo(id_ciclo)
    if ciclo is None:
        return None
    id_parcela = ciclo["id_parcela"]

    # Override SOS/EOS en memoria (sin tocar BD)
    ciclo["sos"] = str(nuevo_sos)
    ciclo["eos"] = str(nuevo_eos)

    # Cargar índices crudos para el nuevo rango
    try:
        dfs_crudos = cargar_indices_desde_bd(
            fecha_inicio=str(nuevo_sos),
            fecha_fin=str(nuevo_eos),
            ids_parcelas=[id_parcela],
        )
    except ValueError:
        return None

    if dfs_crudos is None or dfs_crudos["EVI"].empty:
        return None

    # Preprocesar
    dfs_vpm = preprocesar_indices_vpm(dfs_crudos, lambda_param=lambda_param)

    # Armar dict ciclo_ext (lo que espera el core)
    ciclo_ext = dict(ciclo)
    ciclo_ext["fecha_inicio"] = str(nuevo_sos)

    dfs_vpm_por_parcela = {id_parcela: dfs_vpm}

    # Ejecutar core SIN persistir
    return _ejecutar_prediccion_ventana_core(
        ciclo=ciclo_ext,
        ventana="EOS",
        fecha_ventana=nuevo_eos,
        dfs_vpm_por_parcela=dfs_vpm_por_parcela,
        fecha_hoy=nuevo_eos,
        persistir=False,
    )


# =============================================================================
# Worker diario — consultas de ciclos activos
# =============================================================================

def obtener_ciclos_activos(
    temporada_activa: str,
    fecha_hoy,
) -> list[dict]:
    """
    Retorna ciclos activos de la temporada cuyo ``eos`` aún no ha pasado.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    sql = """
        SELECT id_ciclo, id_parcela, temporada, lswi_max,
               sos, t1, t2, t3, eos, fecha_inicio, fecha_fin
        FROM produccion_acumulada_ciclo
        WHERE temporada = ?
          AND estado_ciclo = 'activo'
          AND (eos IS NULL OR eos >= ?)
        ORDER BY id_ciclo;
    """
    cols = [
        "id_ciclo", "id_parcela", "temporada", "lswi_max",
        "sos", "t1", "t2", "t3", "eos", "fecha_inicio", "fecha_fin",
    ]
    try:
        with closing(get_connection_raw()) as conn:
            rows = conn.execute(
                sql, (temporada_activa, str(fecha_hoy)),
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []
