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
