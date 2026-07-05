# pipeline/motor_prediccion.py — Orquestador de Predicción de Rendimiento
"""
Motor de predicción de rendimiento de maíz para el Valle de Comayagua.

Define flujos de trabajo completos que conectan, en orden, todas las funciones
reutilizables del pipeline: ingesta satelital y climática, preprocesamiento VPM,
detección fenológica de inicio de temporada (SOS), cálculo de GPP y estimación
de rendimiento final.

Uso típico (flujo completo con descarga openEO):
    from pipeline.motor_prediccion import ejecutar_pipeline_completo
    resultados = ejecutar_pipeline_completo(connection, geojson, "2025-05-01", "2025-10-30")

Uso en notebook (datos ya en memoria, sin re-descargar):
    from pipeline.motor_prediccion import calcular_rendimiento_desde_indices
    resultados = calcular_rendimiento_desde_indices(dfs_crudos, dfs_clima, fecha_fin="2025-10-30")
"""
from __future__ import annotations

import openeo
import pandas as pd

from pipeline.ingesta import (
    obtener_datacube_indices_crudo,
    obtener_datos_climaticos_crudo,
    obtener_indices,
    obtener_clima,
)
from pipeline.modulo_vpm import (
    preprocesar_indices_vpm,
    calcular_gpp_vpm,
    calcular_biomasa_y_rendimiento,
)
from pipeline.modulo_fenologico import detectar_sos

# ── Defaults de los parámetros del modelo VPM ─────────────────────────────────
_VPM_DEFAULTS: dict = {
    "epsilon_0":    1.6,
    "t_min":        10.0,
    "t_opt":        30.0,
    "t_max":        45.0,
    "par_fraction": 0.48,
}

# ── Defaults de los parámetros de conversión a rendimiento ────────────────────
_RENDIMIENTO_DEFAULTS: dict = {
    "cue":               0.55,
    "fraccion_carbono":  0.45,
    "harvest_index":     0.48,
}


# =============================================================================
# FUNCIÓN PRIVADA — núcleo compartido de ambos flujos públicos
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
        Fecha de inicio del ciclo ("YYYY-MM-DD"). Se usa como SOS de fallback
        si no se detecta un SOS para una parcela concreta.
    fecha_fin : str
        Fecha de fin del período de evaluación ("YYYY-MM-DD").
    ventana_sos : tuple(str, str) | None
        Ventana de búsqueda de SOS. Si None, se busca en toda la serie.
    config_rendimiento : dict | None
        Sobreescrituras de parámetros para ``calcular_biomasa_y_rendimiento``.
        Claves disponibles: "cue", "fraccion_carbono", "harvest_index".

    Retorna
    -------
    dict con:
        - "fenologia"  : pd.DataFrame — tabla SOS/POS por parcela.
        - "rendimiento": dict — salida de ``calcular_biomasa_y_rendimiento``.
    """
    cfg_rend = {**_RENDIMIENTO_DEFAULTS, **(config_rendimiento or {})}
    df_evi   = dfs_vpm["EVI"]
    df_gpp   = dfs_gpp["GPP"]

    fecha_inicio_ts = pd.Timestamp(fecha_inicio)
    fecha_fin_ts    = pd.Timestamp(fecha_fin)

    # ── 1. Detectar SOS por parcela ───────────────────────────────────────────
    print("\n🌱 Detectando inicio de temporada (SOS) por parcela...")
    registros_fenologia = []
    sos_por_parcela: dict[str, pd.Timestamp] = {}

    for parcela in df_evi.columns:
        info = detectar_sos(
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
        registros_fenologia.append(
            {
                "parcela":     parcela,
                "sos_fecha":   sos_fecha,
                "sos_valor":   info["sos_valor"],
                "pos_fecha":   info["pos_fecha"],
                "pos_valor":   info["pos_valor"],
                "base_valor":  info["base_valor"],
                "amplitud":    info["amplitud"],
                "umbral":      info["umbral"],
            }
        )

    tabla_sos = pd.DataFrame(registros_fenologia).set_index("parcela")
    print(f"   ✔️ SOS detectado para {tabla_sos['sos_fecha'].notna().sum()} / {len(tabla_sos)} parcelas.")

    # ── 2. Recortar GPP al período vegetativo diferenciado por parcela ────────
    print("\n✂️  Recortando GPP al período vegetativo de cada parcela...")
    gpp_recortado_por_parcela: dict[str, pd.Series] = {}

    for parcela in df_gpp.columns:
        sos = sos_por_parcela[parcela]
        mascara = (df_gpp.index >= sos) & (df_gpp.index <= fecha_fin_ts)
        gpp_recortado_por_parcela[parcela] = df_gpp.loc[mascara, parcela]

    # Reunificar en un DataFrame alineado al índice más largo (rellenar con 0)
    # para que calcular_biomasa_y_rendimiento reciba una estructura tabular.
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


# =============================================================================
# FLUJO 1 — Pipeline completo (descarga + procesamiento + predicción)
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
    Flujo de trabajo end-to-end para la predicción de rendimiento de maíz.

    Descarga los datos de Sentinel-2 y AgERA5 mediante openEO y ejecuta el
    pipeline completo hasta la estimación de rendimiento final.

    Parámetros
    ----------
    connection : openeo.Connection
        Conexión activa al backend **CDSE** (openeo.dataspace.copernicus.eu).
        Se usa exclusivamente para la ingesta de índices Sentinel-2.
    connection_fed : openeo.Connection | None
        Conexión activa al backend **federado** (openeofed.dataspace.copernicus.eu).
        Se usa exclusivamente para la ingesta de datos climáticos AgERA5.
        Si es None, se reutiliza ``connection`` (comportamiento legacy, puede
        fallar si el backend CDSE no expone la colección AGERA5).
    geojson_openeo : dict
        GeoJSON FeatureCollection con las parcelas en EPSG:4326.
    fecha_inicio : str
        Inicio del ciclo de cultivo en formato "YYYY-MM-DD".
    fecha_fin : str
        Fin del ciclo de cultivo en formato "YYYY-MM-DD".
    config_cloud_mask : dict | None
        Overrides para la máscara de nubes SCL de Sentinel-2.
        Claves disponibles: kernel1_size, kernel2_size, mask1_values,
        mask2_values, erosion_kernel_size.
    lambda_param : float
        Parámetro de suavizado Whittaker-Eilers (por defecto 4000.0).
    lswi_max : pd.Series | dict[str, float] | None
        LSWI máximo histórico por parcela para calcular W_scalar.
        Si None, se calcula desde la serie actual.
    config_vpm : dict | None
        Overrides de parámetros del modelo VPM.
        Claves disponibles: epsilon_0, t_min, t_opt, t_max, par_fraction.
    config_rendimiento : dict | None
        Overrides de parámetros de conversión a rendimiento.
        Claves disponibles: cue, fraccion_carbono, harvest_index.
    ventana_sos : tuple(str, str) | None
        Ventana de búsqueda de SOS como ("YYYY-MM-DD", "YYYY-MM-DD").
        Si None, se busca en toda la serie temporal.

    Retorna
    -------
    dict con las claves:
        - "indices_crudos" : dict[str, pd.DataFrame] — EVI y LSWI sin procesar.
        - "clima"          : dict[str, pd.DataFrame] — temperatura y radiación AgERA5.
        - "vegetacion"     : dict[str, pd.DataFrame] — EVI, LSWI, FPAR, W_scalar diarios.
        - "gpp"            : dict[str, pd.DataFrame] — PAR, T_scalar, epsilon, APAR, GPP.
        - "fenologia"      : pd.DataFrame — SOS y POS por parcela.
        - "rendimiento"    : dict — npp_diario, biomasa_acumulada, yield_final_tha.
    """
    cfg_vpm = {**_VPM_DEFAULTS, **(config_vpm or {})}

    # El backend federado es el único que expone AgERA5; si no se pasa
    # explícitamente, caer de vuelta a connection con una advertencia.
    conn_clima = connection_fed if connection_fed is not None else connection
    if connection_fed is None:
        import warnings
        warnings.warn(
            "connection_fed no fue proporcionado. Se usará 'connection' para AgERA5, "
            "lo cual puede fallar si el backend CDSE no expone esa colección. "
            "Pasa connection_fed=<conexión al backend federado> para evitar este problema.",
            stacklevel=2,
        )

    print("=" * 60)
    print("🚀 MOTOR DE PREDICCIÓN VPM — INICIO DE PIPELINE COMPLETO")
    print(f"   Período: {fecha_inicio}  →  {fecha_fin}")
    print("=" * 60)

    # ── Paso 1: Ingesta de índices espectrales ─────────────────────────────
    print("\n📡 PASO 1/5 — Ingesta de índices espectrales (Sentinel-2 / CDSE)...")
    dfs_crudos = obtener_indices(
        connection=connection,
        geojson_openeo=geojson_openeo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        config_cloud_mask=config_cloud_mask,
    )

    # ── Paso 2: Ingesta de datos climáticos ───────────────────────────────
    print("\n🌤️  PASO 2/5 — Ingesta de datos climáticos (AgERA5 / backend federado)...")
    dfs_clima = obtener_clima(
        connection=conn_clima,
        geojson_openeo=geojson_openeo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )

    # ── Paso 3: Preprocesamiento VPM ──────────────────────────────────────
    print("\n🔬 PASO 3/5 — Preprocesamiento de índices VPM...")
    dfs_vpm = preprocesar_indices_vpm(
        dfs_vpm_crudos=dfs_crudos,
        lambda_param=lambda_param,
        lswi_max=lswi_max,
    )

    # ── Paso 4: Cálculo de GPP ────────────────────────────────────────────
    print("\n⚡ PASO 4/5 — Cálculo de GPP diario (modelo VPM)...")
    dfs_gpp = calcular_gpp_vpm(
        dfs_vegetacion=dfs_vpm,
        dfs_clima=dfs_clima,
        epsilon_0=cfg_vpm["epsilon_0"],
        t_min=cfg_vpm["t_min"],
        t_opt=cfg_vpm["t_opt"],
        t_max=cfg_vpm["t_max"],
        par_fraction=cfg_vpm["par_fraction"],
    )

    # ── Paso 5: Fenología, recorte y rendimiento ──────────────────────────
    print("\n🌽 PASO 5/5 — Detección de SOS y estimación de rendimiento...")
    resultado_feno_rend = _calcular_fenologia_y_rendimiento(
        dfs_vpm=dfs_vpm,
        dfs_gpp=dfs_gpp,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        ventana_sos=ventana_sos,
        config_rendimiento=config_rendimiento,
    )

    yield_tha  = resultado_feno_rend["rendimiento"]["yield_final_tha"]
    yield_mean = yield_tha.mean()

    print("\n" + "=" * 60)
    print("✅ PIPELINE COMPLETO FINALIZADO")
    print(f"   ✔️ Parcelas evaluadas   : {len(yield_tha)}")
    print(f"   ✔️ Rendimiento promedio : {yield_mean:.3f} t/ha")
    print(f"   ✔️ Rendimiento promedio : {yield_mean * 22.0458:.1f} qq/ha")
    print("=" * 60)

    return {
        "indices_crudos": dfs_crudos,
        "clima":          dfs_clima,
        "vegetacion":     dfs_vpm,
        "gpp":            dfs_gpp,
        "fenologia":      resultado_feno_rend["fenologia"],
        "rendimiento":    resultado_feno_rend["rendimiento"],
    }


# =============================================================================
# FLUJO 2 — A partir de índices ya en memoria (sin re-descargar)
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
    Flujo de trabajo a partir de índices ya descargados en memoria.

    Útil para notebooks y para reintentar el procesamiento sin volver a
    descargar desde openEO. Ejecuta los pasos de preprocesamiento VPM,
    cálculo de GPP, detección fenológica y estimación de rendimiento.

    Parámetros
    ----------
    dfs_crudos : dict[str, pd.DataFrame]
        Índices EVI y LSWI crudos, retornados por ``obtener_datacube_indices_crudo``.
    dfs_clima : dict[str, pd.DataFrame]
        Datos climáticos, retornados por ``obtener_datos_climaticos_crudo``.
    fecha_fin : str
        Fin del período de evaluación en formato "YYYY-MM-DD".
    fecha_inicio : str | None
        Inicio del ciclo. Si None, se infiere como la primera fecha del EVI crudo.
        Se usa como fallback de SOS para parcelas donde no se detecte inicio de temporada.
    lambda_param : float
        Parámetro de suavizado Whittaker-Eilers (por defecto 4000.0).
    lswi_max : pd.Series | dict[str, float] | None
        LSWI máximo histórico por parcela. Si None, se calcula desde la serie actual.
    config_vpm : dict | None
        Overrides de parámetros del modelo VPM.
        Claves disponibles: epsilon_0, t_min, t_opt, t_max, par_fraction.
    config_rendimiento : dict | None
        Overrides de parámetros de conversión a rendimiento.
        Claves disponibles: cue, fraccion_carbono, harvest_index.
    ventana_sos : tuple(str, str) | None
        Ventana de búsqueda de SOS como ("YYYY-MM-DD", "YYYY-MM-DD").
        Si None, se busca en toda la serie temporal.

    Retorna
    -------
    dict con las claves:
        - "vegetacion"  : dict[str, pd.DataFrame] — EVI, LSWI, FPAR, W_scalar diarios.
        - "gpp"         : dict[str, pd.DataFrame] — PAR, T_scalar, epsilon, APAR, GPP.
        - "fenologia"   : pd.DataFrame — SOS y POS por parcela.
        - "rendimiento" : dict — npp_diario, biomasa_acumulada, yield_final_tha.
    """
    cfg_vpm = {**_VPM_DEFAULTS, **(config_vpm or {})}

    # Inferir fecha_inicio si no se provee
    if fecha_inicio is None:
        fecha_inicio = str(dfs_crudos["EVI"].index.min().date())
        print(f"ℹ️  fecha_inicio no especificada. Se infiere: {fecha_inicio}")

    print("=" * 60)
    print("🔁 MOTOR DE PREDICCIÓN VPM — FLUJO DESDE ÍNDICES EN MEMORIA")
    print(f"   Período: {fecha_inicio}  →  {fecha_fin}")
    print("=" * 60)

    # ── Paso 1: Preprocesamiento VPM ──────────────────────────────────────
    print("\n🔬 PASO 1/3 — Preprocesamiento de índices VPM...")
    dfs_vpm = preprocesar_indices_vpm(
        dfs_vpm_crudos=dfs_crudos,
        lambda_param=lambda_param,
        lswi_max=lswi_max,
    )

    # ── Paso 2: Cálculo de GPP ────────────────────────────────────────────
    print("\n⚡ PASO 2/3 — Cálculo de GPP diario (modelo VPM)...")
    dfs_gpp = calcular_gpp_vpm(
        dfs_vegetacion=dfs_vpm,
        dfs_clima=dfs_clima,
        epsilon_0=cfg_vpm["epsilon_0"],
        t_min=cfg_vpm["t_min"],
        t_opt=cfg_vpm["t_opt"],
        t_max=cfg_vpm["t_max"],
        par_fraction=cfg_vpm["par_fraction"],
    )

    # ── Paso 3: Fenología, recorte y rendimiento ──────────────────────────
    print("\n🌽 PASO 3/3 — Detección de SOS y estimación de rendimiento...")
    resultado_feno_rend = _calcular_fenologia_y_rendimiento(
        dfs_vpm=dfs_vpm,
        dfs_gpp=dfs_gpp,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        ventana_sos=ventana_sos,
        config_rendimiento=config_rendimiento,
    )

    yield_tha  = resultado_feno_rend["rendimiento"]["yield_final_tha"]
    yield_mean = yield_tha.mean()

    print("\n" + "=" * 60)
    print("✅ FLUJO COMPLETADO")
    print(f"   ✔️ Parcelas evaluadas   : {len(yield_tha)}")
    print(f"   ✔️ Rendimiento promedio : {yield_mean:.3f} t/ha")
    print(f"   ✔️ Rendimiento promedio : {yield_mean * 22.0458:.1f} qq/ha")
    print("=" * 60)

    return {
        "vegetacion":  dfs_vpm,
        "gpp":         dfs_gpp,
        "fenologia":   resultado_feno_rend["fenologia"],
        "rendimiento": resultado_feno_rend["rendimiento"],
    }
