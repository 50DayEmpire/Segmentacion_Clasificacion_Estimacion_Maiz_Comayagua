# pipeline/flujos_trabajo.py — Orquestador de Predicción de Rendimiento
"""
Motor de predicción de rendimiento de maíz para el Valle de Comayagua.

Define tres flujos de trabajo que conectan, en orden, todas las funciones
reutilizables del pipeline: ingesta con caché, preprocesamiento VPM,
detección fenológica de inicio de temporada (SOS), cálculo de GPP y
estimación de rendimiento final.

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
# Worker diario — consultas de ciclos activos
# =============================================================================

def obtener_ciclos_activos(
    temporada_activa: str,
    fecha_hoy,
) -> list[dict]:
    """
    Retorna ciclos activos (``eos`` IS NULL, ``fecha_inicio`` <= fecha_hoy,
    ``temporada`` = temporada_activa) desde ``produccion_acumulada_ciclo``.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    sql = """
        SELECT id_ciclo, id_parcela, temporada, lswi_max,
               sos, t1, t2, t3, eos, fecha_inicio, fecha_fin
        FROM produccion_acumulada_ciclo
        WHERE eos IS NULL
          AND fecha_inicio IS NOT NULL
          AND fecha_inicio <= ?
          AND temporada = ?
        ORDER BY id_ciclo;
    """
    cols = [
        "id_ciclo", "id_parcela", "temporada", "lswi_max",
        "sos", "t1", "t2", "t3", "eos", "fecha_inicio", "fecha_fin",
    ]
    try:
        with closing(get_connection_raw()) as conn:
            rows = conn.execute(
                sql, (str(fecha_hoy), temporada_activa),
            ).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []
