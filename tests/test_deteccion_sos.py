# tests/test_deteccion_sos.py
import pandas as pd
import numpy as np
from pipeline.modulo_fenologico import detectar_sos


def test_detecta_sos_en_curva_simple():
    fechas = pd.date_range("2024-04-01", "2024-08-01", freq="D")
    dias = np.arange(len(fechas))
    # curva sintética: valle bajo, subida hacia día 40, meseta
    serie = 0.2 + 0.5 / (1 + np.exp(-0.15 * (dias - 40)))

    resultado = detectar_sos(serie, fechas, factor=0.2)

    assert resultado["sos_fecha"] is not None
    # el cruce de umbral debe caer razonablemente antes del punto de inflexión
    assert resultado["sos_fecha"] < fechas[40]


def test_serie_toda_nan_no_lanza_excepcion():
    fechas = pd.date_range("2024-04-01", periods=30, freq="D")
    serie = np.full(30, np.nan)

    resultado = detectar_sos(serie, fechas)

    assert resultado["sos_fecha"] is None


def test_amplitud_negativa_no_detecta_sos():
    # serie descendente: no hay temporada de crecimiento real
    fechas = pd.date_range("2024-04-01", periods=30, freq="D")
    serie = np.linspace(0.6, 0.2, 30)

    resultado = detectar_sos(serie, fechas)

    assert resultado["sos_fecha"] is None
    assert resultado["amplitud"] <= 0


def test_ventana_sos_mas_angosta_que_ventana_busqueda():
    fechas = pd.date_range("2024-01-01", "2024-12-01", freq="D")
    dias = np.arange(len(fechas))
    serie = 0.2 + 0.5 / (1 + np.exp(-0.1 * (dias - 200)))

    resultado = detectar_sos(
        serie, fechas, factor=0.2,
        ventana_busqueda=(fechas[0], fechas[-1]),
        ventana_sos=(pd.Timestamp("2024-04-01"), pd.Timestamp("2024-08-20")),
    )
    # el pico real está fuera de ventana_sos (día 200 ≈ julio), el cruce
    # de subida debería caer dentro de la ventana angosta si el diseño es correcto
    assert resultado["sos_fecha"] is None or (
        pd.Timestamp("2024-04-01") <= resultado["sos_fecha"] <= pd.Timestamp("2024-08-20")
    )

def test_doble_pico_usa_pico_mas_alto_no_el_mas_temprano():
    fechas = pd.date_range("2024-01-01", periods=300, freq="D")
    dias = np.arange(300)
    # pico espurio temprano (maleza) más bajo que el pico real del cultivo
    pico_maleza = 0.3 * np.exp(-((dias - 50) ** 2) / 200)
    pico_cultivo = 0.5 * np.exp(-((dias - 180) ** 2) / 800)
    serie = 0.15 + pico_maleza + pico_cultivo

    resultado = detectar_sos(serie, fechas, factor=0.2)

    # pos_fecha debe anclarse al pico real (día ~180), no al de maleza (día ~50)
    assert abs((resultado["pos_fecha"] - fechas[180]).days) < 20

def test_cold_start_pocas_observaciones_no_lanza_excepcion():
    """
    Escenario cold-start: monitoreo en curso con solo ~12 días desde la
    primera adquisición útil, muy por debajo del punto de inflexión real
    del cultivo (que ocurriría hacia el día ~40 según las curvas sintéticas
    de otros tests). Replica la situación real de las primeras 2-3
    adquisiciones de un ciclo en el pipeline online, donde el "pico"
    detectado es forzosamente un artefacto de borde (el último punto
    disponible), no el pico fenológico real.

    Este test no exige que sos_fecha sea "correcto" -- no hay forma de que
    lo sea con tan pocos datos -- sino que documenta el comportamiento
    esperado: la función debe seguir devolviendo una estructura válida sin
    fallar, con pos_fecha anclado al último punto disponible (evidencia
    del efecto de borde) y una amplitud comprimida, coherente con la
    justificación ya usada para activar el fallback a relative_amplitude
    en estas condiciones.
    """
    fechas = pd.date_range("2024-04-01", periods=12, freq="D")
    dias = np.arange(12)
    # mismo modelo de curva que test_detecta_sos_en_curva_simple (inflexión
    # real en día 40), pero truncada a los primeros 12 días: apenas
    # despegando desde el valle, muy lejos de la meseta real.
    serie = 0.2 + 0.5 / (1 + np.exp(-0.15 * (dias - 40)))

    resultado = detectar_sos(serie, fechas, factor=0.2)

    # no debe fallar ni devolver una estructura incompleta
    assert resultado is not None
    assert set(resultado.keys()) == {
        "sos_fecha", "sos_valor", "pos_fecha", "pos_valor",
        "base_valor", "amplitud", "umbral",
    }

    # con la serie truncada, el "pico" detectado es necesariamente el
    # último punto disponible -- no hay forma de que sea el pico real
    assert resultado["pos_fecha"] == fechas[-1]

    # la amplitud observable en esta ventana truncada debe ser mucho menor
    # que la amplitud real de la curva completa (vmax - vmin ≈ 0.5),
    # evidencia cuantitativa del efecto de borde
    assert resultado["amplitud"] is not None
    assert resultado["amplitud"] < 0.05

    # si detecta un cruce, debe caer estrictamente dentro de la ventana
    # observada, entre el valle y el "pico" artificial -- consistencia
    # estructural básica, sin asumir nada sobre a qué distancia del borde
    if resultado["sos_fecha"] is not None:
        assert fechas[0] <= resultado["sos_fecha"] <= resultado["pos_fecha"]