# tests/test_modulo_vpm.py
import pytest
import numpy as np
import pandas as pd
from pipeline.modulo_vpm import (
    preprocesar_indices_vpm,
    guardar_indices_suavizados,
    calcular_gpp_vpm,
    calcular_biomasa_y_rendimiento,
)


# =========================================================================
# preprocesar_indices_vpm
# =========================================================================

def _df_parcela(dias=100, base=0.3, amp=0.5, inclinacion=0.08, offset=40):
    """Crea un DataFrame con una columna id_1 simulando EVI o LSWI."""
    fechas = pd.date_range("2024-04-01", periods=dias, freq="D")
    t = np.arange(dias)
    valores = base + amp / (1 + np.exp(-inclinacion * (t - offset)))
    return pd.DataFrame({"id_1": valores}, index=fechas)


def test_preprocesar_indices_vpm_estructura_salida():
    dfs = {"EVI": _df_parcela(), "LSWI": _df_parcela(base=0.1, amp=0.3)}
    resultado = preprocesar_indices_vpm(dfs)

    assert set(resultado.keys()) == {"EVI", "LSWI", "FPAR", "W_scalar"}
    for k in ("EVI", "LSWI", "FPAR", "W_scalar"):
        assert isinstance(resultado[k], pd.DataFrame)
        assert not resultado[k].empty


def test_preprocesar_indices_vpm_fpar_igual_evi():
    dfs = {"EVI": _df_parcela(), "LSWI": _df_parcela(base=0.1, amp=0.3)}
    resultado = preprocesar_indices_vpm(dfs)

    pd.testing.assert_frame_equal(resultado["FPAR"], resultado["EVI"])


def test_preprocesar_indices_vpm_w_scalar_en_rango():
    dfs = {"EVI": _df_parcela(), "LSWI": _df_parcela(base=0.1, amp=0.3)}
    resultado = preprocesar_indices_vpm(dfs)

    ws = resultado["W_scalar"]
    assert ws.min().min() >= 0.0
    assert ws.max().max() <= 2.0


def test_preprocesar_indices_vpm_quita_outliers():
    fechas = pd.date_range("2024-04-01", periods=50, freq="D")
    evi = pd.DataFrame({"id_1": np.linspace(0.2, 0.7, 50)}, index=fechas)
    evi.iloc[10] = 5.0  # outlier
    lswi = pd.DataFrame({"id_1": np.linspace(0.1, 0.3, 50)}, index=fechas)

    dfs = {"EVI": evi, "LSWI": lswi}
    resultado = preprocesar_indices_vpm(dfs, lambda_param=10000)

    evi_suave = resultado["EVI"]["id_1"]
    assert np.all(evi_suave.between(-1.0, 1.0, inclusive="both"))


def test_preprocesar_indices_vpm_lswi_max_personalizado():
    dfs = {"EVI": _df_parcela(), "LSWI": _df_parcela(base=0.1, amp=0.3)}
    lswi_max = {"id_1": 0.5}
    resultado = preprocesar_indices_vpm(dfs, lswi_max=lswi_max)

    ws = resultado["W_scalar"]["id_1"]
    esperado = (1.0 + resultado["LSWI"]["id_1"]) / (1.0 + 0.5)
    pd.testing.assert_series_equal(ws, esperado, check_names=False)


def test_preprocesar_indices_vpm_reindexacion_diaria_cierra_gaps():
    fechas = pd.date_range("2024-04-01", periods=30, freq="D")
    fechas_con_gap = fechas.drop(fechas[5:10])
    evi = pd.DataFrame({"id_1": np.linspace(0.2, 0.7, len(fechas_con_gap))}, index=fechas_con_gap)
    lswi = pd.DataFrame({"id_1": np.linspace(0.1, 0.3, len(fechas_con_gap))}, index=fechas_con_gap)

    dfs = {"EVI": evi, "LSWI": lswi}
    resultado = preprocesar_indices_vpm(dfs, lambda_param=10000)

    assert len(resultado["EVI"]) == 30
    assert resultado["EVI"].index.freq == "D"


# =========================================================================
# guardar_indices_suavizados
# =========================================================================

def test_guardar_indices_suavizados_escribe_filas(conn_prueba):
    fechas = pd.date_range("2024-04-01", periods=5, freq="D")
    dfs = {
        "EVI": pd.DataFrame({"id_1": [0.3, 0.35, 0.4, 0.45, 0.5]}, index=fechas),
        "LSWI": pd.DataFrame({"id_1": [0.1, 0.12, 0.14, 0.16, 0.18]}, index=fechas),
    }
    n = guardar_indices_suavizados(id_ciclo=1, id_parcela=1, dfs_vpm=dfs)

    assert n == 5
    filas = conn_prueba.execute(
        "SELECT id_ciclo, id_parcela, fecha, evi, lswi FROM indices_suavizados ORDER BY fecha"
    ).fetchall()
    assert len(filas) == 5
    assert filas[0] == (1, 1, "2024-04-01", 0.3, 0.1)


def test_guardar_indices_suavizados_upsert_no_duplica(conn_prueba):
    fechas = pd.date_range("2024-04-01", periods=3, freq="D")
    dfs = {
        "EVI": pd.DataFrame({"id_1": [0.3, 0.35, 0.4]}, index=fechas),
        "LSWI": pd.DataFrame({"id_1": [0.1, 0.12, 0.14]}, index=fechas),
    }

    guardar_indices_suavizados(id_ciclo=1, id_parcela=1, dfs_vpm=dfs)

    dfs2 = {
        "EVI": pd.DataFrame({"id_1": [0.5, 0.55, 0.6]}, index=fechas),
        "LSWI": pd.DataFrame({"id_1": [0.2, 0.22, 0.24]}, index=fechas),
    }
    guardar_indices_suavizados(id_ciclo=1, id_parcela=1, dfs_vpm=dfs2)

    filas = conn_prueba.execute(
        "SELECT count(*) FROM indices_suavizados WHERE id_ciclo = 1"
    ).fetchone()[0]
    assert filas == 3


def test_guardar_indices_suavizados_sin_columna_parcela_retorna_cero():
    fechas = pd.date_range("2024-04-01", periods=3, freq="D")
    dfs = {
        "EVI": pd.DataFrame({"id_999": [0.3, 0.35, 0.4]}, index=fechas),
        "LSWI": pd.DataFrame({"id_999": [0.1, 0.12, 0.14]}, index=fechas),
    }
    n = guardar_indices_suavizados(id_ciclo=1, id_parcela=1, dfs_vpm=dfs)
    assert n == 0


def test_guardar_indices_suavizados_evi_sin_lswi_escribe_nulo(conn_prueba):
    fechas = pd.date_range("2024-04-01", periods=3, freq="D")
    dfs = {
        "EVI": pd.DataFrame({"id_1": [0.3, 0.35, 0.4]}, index=fechas),
        "LSWI": None,
    }
    n = guardar_indices_suavizados(id_ciclo=1, id_parcela=1, dfs_vpm=dfs)

    assert n == 3
    filas = conn_prueba.execute(
        "SELECT evi, lswi FROM indices_suavizados ORDER BY fecha"
    ).fetchall()
    for evi, lswi in filas:
        assert evi is not None
        assert lswi is None


# =========================================================================
# calcular_gpp_vpm
# =========================================================================

def _df_clima(t_min=15, t_max=35, n_dias=100):
    fechas = pd.date_range("2024-04-01", periods=n_dias, freq="D")
    temp = pd.DataFrame({"id_1": np.linspace(t_min, t_max, n_dias)}, index=fechas)
    ssrd = pd.DataFrame({"id_1": np.full(n_dias, 20e6)}, index=fechas)  # 20 MJ/m²/día
    return {"temperature-mean": temp, "solar-radiation-flux": ssrd}


def _df_veg(fpar_val=0.5, w_scalar_val=0.8, n_dias=100):
    fechas = pd.date_range("2024-04-01", periods=n_dias, freq="D")
    return {
        "FPAR": pd.DataFrame({"id_1": np.full(n_dias, fpar_val)}, index=fechas),
        "W_scalar": pd.DataFrame({"id_1": np.full(n_dias, w_scalar_val)}, index=fechas),
    }


def test_calcular_gpp_vpm_estructura_salida():
    veg = _df_veg()
    clima = _df_clima()

    resultado = calcular_gpp_vpm(veg, clima)

    assert set(resultado.keys()) == {"PAR", "T_scalar", "epsilon", "APAR", "GPP"}
    for k in resultado:
        assert isinstance(resultado[k], pd.DataFrame)
        assert not resultado[k].empty


def test_calcular_gpp_vpm_gpp_positivo():
    veg = _df_veg()
    clima = _df_clima()

    resultado = calcular_gpp_vpm(veg, clima)

    assert (resultado["GPP"]["id_1"] > 0).all()


def test_calcular_gpp_vpm_par_unidades():
    """PAR debe estar en MJ/m²/día: SSRD 20 MJ/m²/día * 0.45 = 9 MJ/m²/día."""
    veg = _df_veg()
    clima = _df_clima()

    resultado = calcular_gpp_vpm(veg, clima)

    par_esperado = 20e6 / 1e6 * 0.45  # SSRD convertido
    assert np.allclose(resultado["PAR"]["id_1"], par_esperado)


def test_calcular_gpp_vpm_t_scalar_cero_en_frio_extremo():
    """Temperatura por debajo de t_min debe dar T_scalar = 0."""
    veg = _df_veg(n_dias=10)
    clima = _df_clima(t_min=2, t_max=10, n_dias=10)

    resultado = calcular_gpp_vpm(veg, clima, t_min=10, t_opt=28, t_max=48)

    assert (resultado["T_scalar"]["id_1"] == 0.0).all()


def test_calcular_gpp_vpm_t_scalar_cero_en_calor_extremo():
    """Temperatura por encima de t_max debe dar T_scalar = 0."""
    veg = _df_veg(n_dias=10)
    clima = _df_clima(t_min=50, t_max=55, n_dias=10)

    resultado = calcular_gpp_vpm(veg, clima, t_min=10, t_opt=28, t_max=48)

    assert (resultado["T_scalar"]["id_1"] == 0.0).all()


def test_calcular_gpp_vpm_t_scalar_en_optimo():
    """En temperatura óptima, T_scalar debe ser cercano a 1."""
    veg = _df_veg(n_dias=10)
    clima = _df_clima(t_min=28, t_max=28, n_dias=10)

    resultado = calcular_gpp_vpm(veg, clima, t_min=10, t_opt=28, t_max=48)

    assert np.allclose(resultado["T_scalar"]["id_1"], 1.0, atol=1e-4)


def test_calcular_gpp_vpm_desalineacion_temporal_reindexa():
    """Índices climáticos desalineados deben reindexarse sin error."""
    fechas_veg = pd.date_range("2024-04-01", periods=100, freq="D")
    fechas_clima = pd.date_range("2024-03-30", periods=104, freq="D")

    veg = {
        "FPAR": pd.DataFrame({"id_1": np.full(100, 0.5)}, index=fechas_veg),
        "W_scalar": pd.DataFrame({"id_1": np.full(100, 0.8)}, index=fechas_veg),
    }
    clima = {
        "temperature-mean": pd.DataFrame({"id_1": np.full(104, 25.0)}, index=fechas_clima),
        "solar-radiation-flux": pd.DataFrame({"id_1": np.full(104, 20e6)}, index=fechas_clima),
    }

    resultado = calcular_gpp_vpm(veg, clima)
    assert len(resultado["GPP"]) == 100


def test_calcular_gpp_vpm_broadcast_columnas():
    """Clima 1 columna, vegetación 2 parcelas → broadcast."""
    fechas = pd.date_range("2024-04-01", periods=50, freq="D")

    veg = {
        "FPAR": pd.DataFrame({"id_1": np.full(50, 0.5), "id_2": np.full(50, 0.6)}, index=fechas),
        "W_scalar": pd.DataFrame({"id_1": np.full(50, 0.8), "id_2": np.full(50, 0.7)}, index=fechas),
    }
    clima = {
        "temperature-mean": pd.DataFrame({"regional": np.full(50, 25.0)}, index=fechas),
        "solar-radiation-flux": pd.DataFrame({"regional": np.full(50, 20e6)}, index=fechas),
    }

    resultado = calcular_gpp_vpm(veg, clima)
    assert list(resultado["GPP"].columns) == ["id_1", "id_2"]
    assert len(resultado["GPP"]) == 50


def test_calcular_gpp_vpm_clima_no_cubre_vegetacion_lanza_error():
    """Error si el rango de clima no cubre el rango de vegetación."""
    fechas_veg = pd.date_range("2024-05-01", periods=100, freq="D")
    fechas_clima = pd.date_range("2024-04-01", periods=20, freq="D")

    veg = {
        "FPAR": pd.DataFrame({"id_1": np.full(100, 0.5)}, index=fechas_veg),
        "W_scalar": pd.DataFrame({"id_1": np.full(100, 0.8)}, index=fechas_veg),
    }
    clima = {
        "temperature-mean": pd.DataFrame({"id_1": np.full(20, 25.0)}, index=fechas_clima),
        "solar-radiation-flux": pd.DataFrame({"id_1": np.full(20, 20e6)}, index=fechas_clima),
    }

    with pytest.raises(ValueError, match="alineaci.n temporal|no cubre"):
        calcular_gpp_vpm(veg, clima)


# =========================================================================
# calcular_biomasa_y_rendimiento
# =========================================================================

def _df_gpp_constante(valor=5.0, n_dias=120):
    fechas = pd.date_range("2024-04-01", periods=n_dias, freq="D")
    return pd.DataFrame({"id_1": np.full(n_dias, valor)}, index=fechas)


def test_calcular_biomasa_y_rendimiento_valores_npp():
    """NPP = GPP * CUE."""
    gpp = _df_gpp_constante(valor=5.0, n_dias=10)
    resultado = calcular_biomasa_y_rendimiento(gpp, cue=0.5)

    pd.testing.assert_frame_equal(resultado["npp_diario"], gpp * 0.5)


def test_calcular_biomasa_y_rendimiento_yield_positivo():
    gpp = _df_gpp_constante(valor=5.0, n_dias=120)
    resultado = calcular_biomasa_y_rendimiento(gpp)

    yield_ = resultado["yield_final_tha"]
    assert yield_["id_1"] > 0


def test_calcular_biomasa_y_rendimiento_consistencia_magnitud():
    """
    GPP constante de 5 g C/m²/día × 120 días × CUE 0.5 × 1/0.45 × 0.48 × 0.01.

    yield (t/ha) = GPP_total * CUE / fraccion_carbono * harvest_index * 0.01
                 = 600 * 0.5 / 0.45 * 0.48 * 0.01
                 = 3.2 t/ha
    """
    gpp = _df_gpp_constante(valor=5.0, n_dias=120)
    resultado = calcular_biomasa_y_rendimiento(gpp, cue=0.5, fraccion_carbono=0.45, harvest_index=0.48)

    esperado = (5.0 * 120) * 0.5 / 0.45 * 0.48 * 0.01
    assert np.isclose(resultado["yield_final_tha"]["id_1"], esperado, atol=1e-4)


def test_calcular_biomasa_y_rendimiento_claves_salida():
    gpp = _df_gpp_constante(n_dias=10)
    resultado = calcular_biomasa_y_rendimiento(gpp)

    assert set(resultado.keys()) == {"npp_diario", "biomasa_acumulada", "yield_final_tha"}
    assert isinstance(resultado["npp_diario"], pd.DataFrame)
    assert isinstance(resultado["biomasa_acumulada"], pd.DataFrame)
    assert isinstance(resultado["yield_final_tha"], pd.Series)


def test_calcular_biomasa_y_rendimiento_biomasa_acumulada_creciente():
    gpp = _df_gpp_constante(valor=5.0, n_dias=50)
    resultado = calcular_biomasa_y_rendimiento(gpp)

    bio = resultado["biomasa_acumulada"]["id_1"]
    assert (bio.diff().dropna() > 0).all()  # estrictamente creciente


def test_calcular_biomasa_y_rendimiento_gpp_vacio_lanza_error():
    gpp = pd.DataFrame()
    with pytest.raises(ValueError, match="vac.o|empty"):
        calcular_biomasa_y_rendimiento(gpp)


def test_calcular_biomasa_y_rendimiento_gpp_con_nan_en_medio():
    """NaN en medio no propaga al yield porque cumsum(skipna=True) lo salta."""
    fechas = pd.date_range("2024-04-01", periods=10, freq="D")
    valores = np.full(10, 5.0)
    valores[4] = np.nan
    gpp = pd.DataFrame({"id_1": valores}, index=fechas)

    resultado = calcular_biomasa_y_rendimiento(gpp)

    assert not np.isnan(resultado["yield_final_tha"]["id_1"])


def test_calcular_biomasa_y_rendimiento_varias_parcelas():
    fechas = pd.date_range("2024-04-01", periods=100, freq="D")
    gpp = pd.DataFrame({
        "id_1": np.full(100, 5.0),
        "id_2": np.full(100, 4.0),
    }, index=fechas)

    resultado = calcular_biomasa_y_rendimiento(gpp, cue=0.5, fraccion_carbono=0.45, harvest_index=0.48)

    esperado_1 = (5.0 * 100) * 0.5 / 0.45 * 0.48 * 0.01
    esperado_2 = (4.0 * 100) * 0.5 / 0.45 * 0.48 * 0.01
    assert np.isclose(resultado["yield_final_tha"]["id_1"], esperado_1, atol=1e-4)
    assert np.isclose(resultado["yield_final_tha"]["id_2"], esperado_2, atol=1e-4)

def test_calcular_gpp_vpm_y_rendimiento_serie_manual_5dias():
    """
    Serie sintética de 5 días validada con cálculo manual independiente
    (ver tabla en la revisión de pruebas). Valores exactos, no solo rangos.
    """
    fechas = pd.date_range("2024-04-01", periods=5, freq="D")

    veg = {
        "FPAR": pd.DataFrame({"id_1": [0.40, 0.42, 0.44, 0.46, 0.48]}, index=fechas),
        "W_scalar": pd.DataFrame(
            {"id_1": [0.916667, 0.933333, 0.950000, 0.966667, 0.983333]},
            index=fechas,
        ),
    }
    clima = {
        "temperature-mean": pd.DataFrame(
            {"id_1": [20.0, 22.0, 24.0, 26.0, 28.0]}, index=fechas
        ),
        "solar-radiation-flux": pd.DataFrame(
            {"id_1": [15e6, 16e6, 17e6, 18e6, 19e6]}, index=fechas
        ),
    }

    resultado = calcular_gpp_vpm(
        veg, clima, t_min=10, t_opt=28, t_max=48, epsilon_0=2.5
    )

    gpp_esperado = np.array(
        [5.036337, 6.326069, 7.630875, 8.903326, 10.089000]
    )
    assert np.allclose(resultado["GPP"]["id_1"].values, gpp_esperado, atol=1e-4)

    # Cadena hasta rendimiento
    rend = calcular_biomasa_y_rendimiento(
        resultado["GPP"], cue=0.5, fraccion_carbono=0.45, harvest_index=0.48
    )
    assert np.isclose(rend["yield_final_tha"]["id_1"], 0.202590, atol=1e-4)