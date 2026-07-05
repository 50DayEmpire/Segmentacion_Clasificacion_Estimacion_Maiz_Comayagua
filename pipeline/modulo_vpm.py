# pipeline/modulo_vpm.py — Procesamiento y ecuaciones VPM
"""
Módulo para el preprocesamiento de índices espectrales (EVI, LSWI)
y el cálculo de ecuaciones base del modelo Vegetation Photosynthesis Model (VPM).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from utils.aplicar_whittaker import aplicar_whittaker_series


def preprocesar_indices_vpm(
    dfs_vpm_crudos: dict[str, pd.DataFrame],
    lambda_param: float = 4000.0,
    lswi_max: pd.Series | dict[str, float] | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Filtra valores atípicos, reindexa diariamente, suaviza mediante Whittaker-Eilers
    y calcula FPAR y W_scalar para las parcelas del estudio.

    Parámetros
    ----------
    dfs_vpm_crudos : dict[str, pd.DataFrame]
        Diccionario con DataFrames de EVI y LSWI crudos de Sentinel-2.
        Claves: "EVI", "LSWI".
    lambda_param : float, opcional
        Parámetro de suavizado de Whittaker (por defecto 4000.0).
    lswi_max : pd.Series | dict[str, float] | None, opcional
        LSWI máximo histórico por parcela. Si es None, se calcula como el máximo
        de la serie temporal diaria suavizada para cada parcela.

    Retorna
    -------
    dict[str, pd.DataFrame]
        Diccionario con DataFrames indexados diariamente:
        - "EVI": EVI suavizado.
        - "LSWI": LSWI suavizado.
        - "FPAR": Fracción de radiación fotosintéticamente activa absorbida (1.0 * EVI).
        - "W_scalar": Factor de estrés hídrico.
    """
    # ── 1. Filtrado de valores atípicos ───────────────────────────────────────
    # Los valores de EVI y LSWI válidos se sitúan en [-1.0, 1.0].
    # Si un índice en una parcela y fecha está fuera de rango, se invalida (NaN)
    # en ambos índices para esa misma fecha (se considera contaminado).
    # Se omiten los NaNs originales en la evaluación para evitar propagaciones erróneas.
    df_evi = dfs_vpm_crudos["EVI"].copy()
    df_lswi = dfs_vpm_crudos["LSWI"].copy()

    es_atipico_evi = (df_evi < -1.0) | (df_evi > 1.0)
    es_atipico_lswi = (df_lswi < -1.0) | (df_lswi > 1.0)
    mascara_ruido = es_atipico_evi | es_atipico_lswi

    df_evi = df_evi.mask(mascara_ruido, np.nan)
    df_lswi = df_lswi.mask(mascara_ruido, np.nan)

    # ── 2. Reindexación Diaria ────────────────────────────────────────────────
    # Genera la cuadrícula temporal densa (frecuencia diaria 'D')
    rango_diario = pd.date_range(
        start=df_evi.index.min(),
        end=df_evi.index.max(),
        freq="D"
    )

    dfs_diarios_nan = {
        "EVI": df_evi.reindex(rango_diario),
        "LSWI": df_lswi.reindex(rango_diario)
    }

    # ── 3. Suavizado y relleno de gaps con Whittaker ─────────────────────────
    dfs_diarios_limpios = aplicar_whittaker_series(dfs_diarios_nan, lambda_param=lambda_param)

    df_evi_diario = dfs_diarios_limpios["EVI"]
    df_lswi_diario = dfs_diarios_limpios["LSWI"]

    # ── 4. Cálculo de W_scalar y FPAR ─────────────────────────────────────────
    print("\n🌾 Calculando FPAR y Factor de Estrés Hídrico Diario (W_scalar)...")

    # Obtención de LSWI_max (máximo histórico por parcela)
    if lswi_max is None:
        lswi_max_por_parcela = df_lswi_diario.max()
    elif isinstance(lswi_max, dict):
        lswi_max_por_parcela = pd.Series(lswi_max)
    else:
        lswi_max_por_parcela = lswi_max

    # W_scalar diario = (1 + LSWI_diario) / (1 + LSWI_max)
    df_w_scalar = (1.0 + df_lswi_diario) / (1.0 + lswi_max_por_parcela)

    # FPAR diario = 1.0 * EVI_diario Xiao 2004
    df_fpar = 1.0 * df_evi_diario

    num_parc = len(df_evi_diario.columns)
    print("\n✅ Métricas base del VPM calculadas y consolidadas a nivel DIARIO:")
    print(f"   ✔️ Total de Parcelas Procesadas: {num_parc}")
    print(f"   ✔️ FPAR Diario (Máx Global): {df_fpar.max().max():.3f}")
    print(f"   ✔️ W_scalar Diario (Mín/Máx Global): {df_w_scalar.min().min():.3f} / {df_w_scalar.max().max():.3f}")

    return {
        "EVI": df_evi_diario,
        "LSWI": df_lswi_diario,
        "FPAR": df_fpar,
        "W_scalar": df_w_scalar,
    }

def guardar_indices_suavizados(
    id_ciclo: int,
    id_parcela: int,
    dfs_vpm: dict[str, pd.DataFrame],
) -> int:
    """
    Upsert de EVI/LSWI suavizados en ``indices_suavizados``.

    Parámetros
    ----------
    id_ciclo : int
        Ciclo de producción al que pertenecen los índices.
    id_parcela : int
        Parcela procesada.
    dfs_vpm : dict[str, pd.DataFrame]
        Salida de ``preprocesar_indices_vpm``.

    Retorna
    -------
    int
        Número de filas escritas (0 si no hay datos válidos).
    """
    from contextlib import closing

    col = f"id_{id_parcela}"
    df_evi  = dfs_vpm.get("EVI")
    df_lswi = dfs_vpm.get("LSWI")

    if df_evi is None or col not in df_evi.columns:
        return 0

    sql = """
        INSERT INTO indices_suavizados (id_ciclo, fecha, id_parcela, evi, lswi)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (id_ciclo, fecha) DO UPDATE SET
            evi  = excluded.evi,
            lswi = excluded.lswi;
    """
    filas = []
    for fecha, evi_val in df_evi[col].items():
        lswi_val = None
        if (
            df_lswi is not None
            and col in df_lswi.columns
            and fecha in df_lswi.index
        ):
            lswi_val = float(df_lswi.loc[fecha, col])
        filas.append((
            id_ciclo,
            str(pd.Timestamp(fecha).date()),
            id_parcela,
            float(evi_val),
            lswi_val,
        ))

    if not filas:
        return 0

    with closing(get_connection_raw()) as conn:
        with conn:
            conn.executemany(sql, filas)

    return len(filas)

def calcular_gpp_vpm(
    dfs_vegetacion: dict[str, pd.DataFrame],
    dfs_clima: dict[str, pd.DataFrame],
    epsilon_0: float = 3.12, #g C/MJ PAR — Maíz C4 (Jin 2015, kalfas 2011)
    t_min: float = 10.0, #Kalfas 2011
    t_opt: float = 28.0, #Kalfas 2011
    t_max: float = 48.0, #Kalfas 2011
    par_fraction: float = 0.45, #Xiao 2003
) -> dict[str, pd.DataFrame]:
    """
    Calcula la Producción Primaria Bruta (GPP) diaria continua usando el modelo VPM.

    Parámetros
    ----------
    dfs_vegetacion : dict[str, pd.DataFrame]
        Diccionario con las series preprocesadas (retornado por preprocesar_indices_vpm).
        Debe contener: "FPAR" y "W_scalar".
    dfs_clima : dict[str, pd.DataFrame]
        Diccionario con los datos climáticos crudos o preprocesados de AgERA5.
        Debe contener: "temperature-mean" y "solar-radiation-flux".
    epsilon_0 : float, opcional
        Máxima eficiencia fotosintética teórica (por defecto 3.12 para Maíz C4).
    t_min : float, opcional
        Temperatura mínima biológica (por defecto 10.0 °C).
    t_opt : float, opcional
        Temperatura óptima de fotosíntesis (por defecto 28.0 °C).
    t_max : float, opcional
        Temperatura máxima biológica (por defecto 48.0 °C).
    par_fraction : float, opcional
        Fracción de radiación solar fotosintéticamente activa (por defecto 0.45).

    Retorna
    -------
    dict[str, pd.DataFrame]
        Diccionario con DataFrames indexados diariamente:
        - "PAR": Radiación fotosintéticamente activa (MJ/m²/día).
        - "T_scalar": Escalar de temperatura de fotosíntesis.
        - "epsilon": Eficiencia fotosintética real diaria (epsilon_g).
        - "APAR": Radiación fotosintéticamente activa absorbida.
        - "GPP": Producción Primaria Bruta (g C/m²/día).
    """
    df_fpar = dfs_vegetacion["FPAR"]
    df_w_scalar = dfs_vegetacion["W_scalar"]

    df_t2m = dfs_clima["temperature-mean"]
    df_ssrd = dfs_clima["solar-radiation-flux"]

    # ── 1. Validación de Alineación Temporal ──────────────────────────────────
    # fpar viene de la serie de vegetación suavizada por whittaker a intervalo diario.
    # Si las series de clima y vegetación no coinciden exactamente, validamos solapamiento.
    if not df_fpar.index.equals(df_t2m.index) or not df_fpar.index.equals(df_ssrd.index):
        fpar_min, fpar_max = df_fpar.index.min(), df_fpar.index.max()
        clima_min, clima_max = df_t2m.index.min(), df_t2m.index.max()

        # Si el rango de clima no cubre el rango de vegetación, levantamos un error
        if clima_min > fpar_min or clima_max < fpar_max:
            raise ValueError(
                f"Error de alineación temporal: El rango del clima "
                f"[{clima_min.date()} a {clima_max.date()}] no cubre completamente el "
                f"rango de vegetación [{fpar_min.date()} a {fpar_max.date()}]."
            )

        print("⚠️ Advertencia: Las fechas de clima y vegetación no coinciden exactamente. Aplicando reindexación 'nearest' como fallback.")
        df_t2m_diario = df_t2m.reindex(df_fpar.index, method="nearest")
        df_ssrd_diario = df_ssrd.reindex(df_fpar.index, method="nearest")
    else:
        df_t2m_diario = df_t2m
        df_ssrd_diario = df_ssrd

    # ── 2. Alineación Espacial (Broadcasting de parcelas) ─────────────────────
    columnas_parcelas = df_fpar.columns

    # Si las columnas de clima no coinciden exactamente con las de fpar, o si el
    # clima tiene una sola columna regional, hacemos broadcast explícito.
    if not df_t2m_diario.columns.equals(columnas_parcelas):
        df_t2m_aligned = pd.DataFrame(
            np.tile(df_t2m_diario.iloc[:, 0].values, (len(columnas_parcelas), 1)).T,
            index=df_fpar.index,
            columns=columnas_parcelas
        )
    else:
        df_t2m_aligned = df_t2m_diario

    if not df_ssrd_diario.columns.equals(columnas_parcelas):
        df_ssrd_aligned = pd.DataFrame(
            np.tile(df_ssrd_diario.iloc[:, 0].values, (len(columnas_parcelas), 1)).T,
            index=df_fpar.index,
            columns=columnas_parcelas
        )
    else:
        df_ssrd_aligned = df_ssrd_diario

    # ── 3. Conversión de Energía Diaria (PAR) ─────────────────────────────────
    # Convertir SSRD de J/m²/día a MJ/m²/día (dividido entre 1e6) y extraer PAR
    df_par = (df_ssrd_aligned / 1e6) * par_fraction

    # ── 4. Cálculo de T_scalar ────────────────────────────────────────────────
    num_t = (df_t2m_aligned - t_min) * (df_t2m_aligned - t_max)
    den_t = num_t - ((df_t2m_aligned - t_opt) ** 2)

    # Evitar divisiones por cero
    den_t = den_t.replace(0.0, np.nan)

    df_t_scalar = num_t / den_t
    df_t_scalar = df_t_scalar.clip(0.0, 1.0).fillna(0.0)

    # Restricciones biológicas por frío o calor extremo
    df_t_scalar[df_t2m_aligned < t_min] = 0.0
    df_t_scalar[df_t2m_aligned > t_max] = 0.0

    # ── 5. Cálculo de Eficiencia Fotosintética Real (epsilon) ─────────────────
    df_epsilon = epsilon_0 * df_t_scalar * df_w_scalar

    # ── 6. Cálculo de APAR y GPP ──────────────────────────────────────────────
    df_apar = df_fpar * df_par
    df_gpp = df_epsilon * df_apar  # g C / m² / día

    print("\n✅ ¡Pipeline VPM DIARIO COMPLETADO EXITOSAMENTE!")
    print(f"   ✔️ Horizonte temporal continuo evaluado: {df_gpp.shape[0]} días.")
    print(f"   ✔️ Temperatura media registrada en la zona: {df_t2m_aligned.mean().mean():.2f} °C")
    print(f"   ✔️ GPP Promedio diario del Maíz: {df_gpp.mean().mean():.3f} g C/m²/día")
    print(f"   ✔️ GPP Máximo diario alcanzado: {df_gpp.max().max():.2f} g C/m²/día")

    return {
        "PAR": df_par,
        "T_scalar": df_t_scalar,
        "epsilon": df_epsilon,
        "APAR": df_apar,
        "GPP": df_gpp,
    }

def calcular_biomasa_y_rendimiento(
    df_gpp_recortado: pd.DataFrame,
    cue: float = 0.5, #Zhang 2009
    fraccion_carbono: float = 0.45, #IPCC 1997
    harvest_index: float = 0.48 #Estimación inicial razonable para Maíz (C4) en Comayagua
) -> dict:
    """
    Calcula la Productividad Primaria Neta (NPP), la dinámica de acumulación
    de biomasa seca y el rendimiento final de grano (Yield) para múltiples parcelas.

    Parameters
    ----------
    df_gpp_recortado : pd.DataFrame
        DataFrame con DatetimeIndex diario, conteniendo únicamente las fechas
        del ciclo de cultivo ya recortadas. Cada columna es una parcela.
        Unidades: g C / m² / día.
    cue : float, opcional
        Eficiencia de Uso del Carbono (Carbon Use Efficiency: NPP/GPP).
        Por defecto 0.55 para Maíz (C4).
    fraccion_carbono : float, opcional
        Proporción de carbono elemental en la materia seca de la planta.
        Por defecto 0.45 (45%).
    harvest_index : float, opcional
        Índice de Cosecha (HI). Proporción de biomasa aérea que se convierte en grano.
        Por defecto 0.48 (48%).

    Returns
    -------
    dict[str, pd.DataFrame o pd.Series]
        Un diccionario con los siguientes componentes del modelo agro-ecológico:
        - 'npp_diario': DataFrame con la dinámica de NPP (g C / m² / día).
        - 'biomasa_acumulada': DataFrame con la biomasa seca total acumulada día a día (g MS / m²).
        - 'yield_final_tha': Serie con el rendimiento comercial final por parcela (t/ha).
    """
    # Validar que no se pasen DataFrames vacíos tras el recorte temporal
    if df_gpp_recortado.empty:
        raise ValueError("El DataFrame de GPP proporcionado está vacío. Verifica las fechas de recorte.")

    # 1. Calcular NPP Diario (g C / m² / día)
    # Colapsa el carbono bruto restando la respiración autótrofa de la planta
    df_npp = df_gpp_recortado * cue

    # 2. Calcular el incremento diario de Biomasa Seca Total (g MS / m² / día)
    # Convertimos los gramos de Carbono puro a gramos de Tejido Vegetal Seco
    df_biomasa_diaria = df_npp / fraccion_carbono

    # 3. Simular el crecimiento acumulativo del cultivo (Integral Temporal)
    # .cumsum() realiza la suma acumulada consecutiva a lo largo de las filas (eje temporal)
    df_biomasa_acumulada = df_biomasa_diaria.cumsum()

    # 4. Extraer la Biomasa Máxima alcanzada al final del ciclo de corte
    # .iloc[-1] toma directamente la última fila del DataFrame acumulado (Día de Cosecha/Madurez)
    biomasa_final_g_m2 = df_biomasa_acumulada.iloc[-1]

    # 5. Calcular el Rendimiento de Grano Final
    # Multiplicamos la biomasa por el Harvest Index y convertimos de (g / m²) a (t / ha)
    # Factor de conversión: (1 g/m² * 10,000 m²/ha) / 1,000,000 g/t = 0.01
    yield_final_tha = biomasa_final_g_m2 * harvest_index * 0.01

    # Nombrar la serie resultante para mantener el orden en el pipeline
    yield_final_tha.name = "Yield (t/ha)"

    return {
        "npp_diario": df_npp,
        "biomasa_acumulada": df_biomasa_acumulada,
        "yield_final_tha": yield_final_tha
    }