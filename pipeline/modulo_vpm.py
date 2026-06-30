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
    # en ambos índices para esa misma fecha.
    df_evi = dfs_vpm_crudos["EVI"].copy()
    df_lswi = dfs_vpm_crudos["LSWI"].copy()

    mascara_valida = (df_evi >= -1.0) & (df_evi <= 1.0) & (df_lswi >= -1.0) & (df_lswi <= 1.0)

    df_evi = df_evi.where(mascara_valida, np.nan)
    df_lswi = df_lswi.where(mascara_valida, np.nan)

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

    # FPAR diario = 1.0 * EVI_diario
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


def calcular_gpp_vpm(
    dfs_vegetacion: dict[str, pd.DataFrame],
    dfs_clima: dict[str, pd.DataFrame],
    epsilon_0: float = 1.6,
    t_min: float = 10.0,
    t_opt: float = 30.0,
    t_max: float = 45.0,
    par_fraction: float = 0.48,
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
        Máxima eficiencia fotosintética teórica (por defecto 1.6 para Maíz C4).
    t_min : float, opcional
        Temperatura mínima biológica (por defecto 10.0 °C).
    t_opt : float, opcional
        Temperatura óptima de fotosíntesis (por defecto 30.0 °C).
    t_max : float, opcional
        Temperatura máxima biológica (por defecto 45.0 °C).
    par_fraction : float, opcional
        Fracción de radiación solar fotosintéticamente activa (por defecto 0.48).

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
