import numpy as np
import pandas as pd

def detectar_sos(
    serie,
    fechas,
    factor=0.2,
    metodo="seasonal_amplitude",
    ventana_busqueda=None,
    ventana_sos=None,
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
    ventana_sos : tuple(datetime, datetime) o None
        Si se provee, restringe adicionalmente la aceptación del cruce de SOS
        a este sub-rango (ej. ventana de siembra institucional + buffer de
        emergencia), independiente de ventana_busqueda usada para pico/base.
        Si es None, se usa ventana_busqueda también para el cruce de SOS.

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

    # Ventana angosta: restringe adicionalmente dónde se acepta el cruce de SOS
    if ventana_sos is not None:
        ini_sos, fin_sos = pd.to_datetime(ventana_sos[0]), pd.to_datetime(ventana_sos[1])
        slope_izq_validos = slope_izq_validos.loc[
            (slope_izq_validos.index >= ini_sos) & (slope_izq_validos.index <= fin_sos)
        ]

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

def detectar_sos_por_parcela(
    resultado_preprocesamiento: dict[str, pd.DataFrame],
    indice: str = "EVI",
    factor: float = 0.2,
    metodo: str = "seasonal_amplitude",
    ventanas_busqueda: dict[str, tuple] | tuple | None = None,
) -> pd.DataFrame:
    """
    Ejecuta detectar_sos para cada parcela presente en el resultado de
    preprocesar_indices_vpm, y consolida los resultados en un DataFrame.

    Parámetros
    ----------
    resultado_preprocesamiento : dict[str, pd.DataFrame]
        Salida de preprocesar_indices_vpm.
    indice : str, opcional
        "EVI" o "LSWI" (por defecto "EVI").
    factor : float, opcional
        Ver detectar_sos.
    metodo : str, opcional
        Ver detectar_sos.
    ventanas_busqueda : dict[str, tuple] | tuple | None, opcional
        - dict: mapea id_parcela -> (fecha_ini, fecha_fin), para ventanas
        específicas por parcela (ej. centradas en mediana histórica de SOS).
        - tuple: misma ventana aplicada a todas las parcelas.
        - None: sin restricción de ventana.

    Retorna
    -------
    pd.DataFrame
        Una fila por parcela, columnas: id_parcela, sos_fecha, sos_valor,
        pos_fecha, pos_valor, base_valor, amplitud, umbral.
        Parcelas sin datos válidos quedan con columnas en None/NaN pero
        siempre aparecen en el resultado (no se descartan silenciosamente).
    """
    df = resultado_preprocesamiento[indice]
    filas = []

    for id_parcela in df.columns:
        try:
            serie, fechas = extraer_serie_para_sos(
                resultado_preprocesamiento, id_parcela, indice=indice
            )
        except ValueError:
            # Parcela sin ninguna observación válida en el rango disponible
            filas.append({"id_parcela": id_parcela, "sos_fecha": None,
                        "sos_valor": None, "pos_fecha": None, "pos_valor": None,
                        "base_valor": None, "amplitud": None, "umbral": None})
            continue

        if isinstance(ventanas_busqueda, dict):
            ventana = ventanas_busqueda.get(id_parcela)
        else:
            ventana = ventanas_busqueda

        resultado = detectar_sos(
            serie=serie, fechas=fechas, factor=factor,
            metodo=metodo, ventana_busqueda=ventana,
        )
        resultado["id_parcela"] = id_parcela
        filas.append(resultado)

    columnas_orden = ["id_parcela", "sos_fecha", "sos_valor", "pos_fecha",
                    "pos_valor", "base_valor", "amplitud", "umbral"]
    return pd.DataFrame(filas)[columnas_orden]


#===================================================================================================================
#                                     Experimental
#===================================================================================================================
from scipy.signal import find_peaks

def segmentar_ciclos(serie: pd.Series, distancia_min_dias: int = 90,
                      prominencia_min: float = 0.15) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Segmenta una serie suavizada multi-anual (EVI o LSWI, post-Whittaker)
    en ciclos individuales, delimitados por valles consecutivos. De carácter
    retroactivo.

    Parámetros
    ----------
    serie : pd.Series
        Índice suavizado, DatetimeIndex diario, sin NaN.
    distancia_min_dias : int
        Separación mínima entre dos valles consecutivos, para evitar que
        una caída transitoria (nube residual, racha seca corta) se
        confunda con el fin real de un ciclo. Debe ser menor que la
        duración esperada de un ciclo (120 días) pero mayor que cualquier
        fluctuación de corto plazo esperada dentro del ciclo.
    prominencia_min : float
        Profundidad mínima del valle relativa a sus vecinos, en las
        mismas unidades que `serie` (EVI/LSWI). Filtra valles poco
        profundos que no representan un verdadero fin de ciclo
        (suelo desnudo / rastrojo) sino ruido dentro de la temporada.

    Retorna
    -------
    list[tuple[pd.Timestamp, pd.Timestamp]]
        Lista de (fecha_inicio_segmento, fecha_fin_segmento), uno por
        cada ciclo candidato detectado entre valles consecutivos.
    """
    valores = serie.to_numpy()
    fechas = serie.index

    valles_idx, propiedades = find_peaks(
        -valores,
        distance=distancia_min_dias,
        prominence=prominencia_min,
    )

    if len(valles_idx) < 2:
        # No hay suficientes valles para delimitar un ciclo completo;
        # toda la serie es un único segmento candidato.
        return [(fechas[0], fechas[-1])]

    segmentos = []
    for i in range(len(valles_idx) - 1):
        inicio = fechas[valles_idx[i]]
        fin = fechas[valles_idx[i + 1]]
        segmentos.append((inicio, fin))

    return segmentos