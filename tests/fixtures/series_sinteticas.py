# tests/fixtures/series_sinteticas.py
import numpy as np
import pandas as pd


def serie_con_gap_nubes(dias_totales=150, inicio_gap=40, largo_gap=15):
    fechas = pd.date_range("2024-04-01", periods=dias_totales, freq="D")
    dias = np.arange(dias_totales)
    serie = 0.2 + 0.5 / (1 + np.exp(-0.1 * (dias - 60)))
    serie[inicio_gap:inicio_gap + largo_gap] = np.nan
    return pd.Series(serie, index=fechas)


def serie_cold_start_sin_historico():
    # solo 20 días de datos, sin ciclo anterior disponible
    fechas = pd.date_range("2024-04-01", periods=20, freq="D")
    return pd.Series(np.linspace(0.2, 0.28, 20), index=fechas)