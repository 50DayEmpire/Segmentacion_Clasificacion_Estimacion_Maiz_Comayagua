import numpy as np
import pandas as pd


def detectar_sos(
    serie,
    fechas,
    factor=0.2,
    metodo="seasonal_amplitude",
    ventana_busqueda=None,
):
    """
    Detecta el Start of Season (SOS) en una serie temporal de un índice de vegetación
    (EVI o LSWI) a nivel de parcela, replicando la lógica de TIMESAT 3.3 usada en
    phenolopy.get_sos, pero de forma ligera (sin xarray/datacube/dask).

    Parámetros
    ----------
    serie : array-like (1D)
        Valores del índice ya suavizado (post-Whittaker), ordenados cronológicamente.
    fechas : array-like de datetime (1D), misma longitud que `serie`
        Fechas correspondientes a cada observación.
    factor : float, entre 0 y 1
        Fracción de la amplitud (pico - base) usada como umbral de SOS.
        Factor cercano a 0 -> SOS más cerca del valle (siembra).
        Factor cercano a 1 -> SOS más cerca del pico.
    metodo : str
        'seasonal_amplitude' (único implementado en esta versión ligera;
        equivalente al método por defecto de TIMESAT).
    ventana_busqueda : tuple(datetime, datetime) o None
        Si se provee, restringe la búsqueda de pico y SOS a esta ventana de fechas
        (ej. calendario primera/postrera de DICTA), evitando falsos positivos por
        verdor fuera de ciclo.

    Retorna
    -------
    dict con:
        'sos_fecha'   : fecha detectada de inicio de temporada (o None si no se detecta)
        'sos_valor'   : valor del índice en sos_fecha
        'pos_fecha'   : fecha del pico (peak of season) usado como referencia
        'pos_valor'   : valor del índice en el pico
        'base_valor'  : valor base (valle) usado en el cálculo de amplitud
        'amplitud'    : amplitud (pico - base)
        'umbral'      : valor de índice usado como umbral de SOS
    """

    if metodo != "seasonal_amplitude":
        raise NotImplementedError(
            f"Método '{metodo}' no implementado en esta versión ligera. "
            "Use 'seasonal_amplitude'."
        )

    if not (0 <= factor <= 1):
        raise ValueError("El parámetro 'factor' debe estar entre 0 y 1.")

    s = pd.Series(data=np.asarray(serie, dtype=float), index=pd.to_datetime(fechas))
    s = s.sort_index()

    if s.isna().all():
        return {
            "sos_fecha": None, "sos_valor": None,
            "pos_fecha": None, "pos_valor": None,
            "base_valor": None, "amplitud": None, "umbral": None,
        }

    # Restringir a ventana de calendario (primera/postrera) si se especifica
    if ventana_busqueda is not None:
        ini, fin = pd.to_datetime(ventana_busqueda[0]), pd.to_datetime(ventana_busqueda[1])
        s = s.loc[(s.index >= ini) & (s.index <= fin)]

    if s.empty or s.isna().all():
        return {
            "sos_fecha": None, "sos_valor": None,
            "pos_fecha": None, "pos_valor": None,
            "base_valor": None, "amplitud": None, "umbral": None,
        }

    # --- Peak of season (pos): valor y fecha máximos dentro de la ventana ---
    pos_fecha = s.idxmax()
    pos_valor = s.loc[pos_fecha]

    # --- Base (bse): valor mínimo en la pendiente izquierda (antes del pico) ---
    slope_izq = s.loc[s.index <= pos_fecha]
    if slope_izq.empty:
        base_valor = s.min()
    else:
        base_valor = slope_izq.min()

    # --- Amplitud de temporada (aos) ---
    amplitud = pos_valor - base_valor
    if amplitud <= 0 or pd.isna(amplitud):
        return {
            "sos_fecha": None, "sos_valor": None,
            "pos_fecha": pos_fecha, "pos_valor": pos_valor,
            "base_valor": base_valor, "amplitud": amplitud, "umbral": None,
        }

    # --- Umbral de SOS: base + factor * amplitud (método seasonal_amplitude, TIMESAT) ---
    umbral = base_valor + factor * amplitud

    # --- Buscar primera fecha en la pendiente izquierda donde se cruza el umbral hacia arriba ---
    slope_izq_validos = slope_izq.dropna()
    cruce = slope_izq_validos[slope_izq_validos >= umbral]

    if cruce.empty:
        sos_fecha, sos_valor = None, None
    else:
        sos_fecha = cruce.index[0]
        sos_valor = cruce.iloc[0]

    return {
        "sos_fecha": sos_fecha,
        "sos_valor": sos_valor,
        "pos_fecha": pos_fecha,
        "pos_valor": pos_valor,
        "base_valor": base_valor,
        "amplitud": amplitud,
        "umbral": umbral,
    }