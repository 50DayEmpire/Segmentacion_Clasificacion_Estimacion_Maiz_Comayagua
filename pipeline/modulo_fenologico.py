from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta

import numpy as np
import pandas as pd

from config import DIAS_VENTANAS
from utils.conexionDB import get_connection_raw

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

def extraer_serie_para_sos(
    resultado_preprocesamiento: dict,
    id_parcela,
    indice: str = "EVI",
) -> tuple:
    """
    Extrae la serie de valores y su índice de fechas para una parcela dada,
    a partir del dict devuelto por ``preprocesar_indices_vpm``.

    Parámetros
    ----------
    resultado_preprocesamiento : dict[str, pd.DataFrame]
        Salida de preprocesar_indices_vpm (claves: "EVI", "LSWI", etc.).
    id_parcela : str | int
        Nombre de columna tal como aparece en el DataFrame (ej. "id_0" o 0).
    indice : str
        Clave del dict a usar, por defecto "EVI".

    Retorna
    -------
    tuple(np.ndarray, pd.DatetimeIndex)
        (valores, fechas) listos para pasar a detectar_sos.

    Raises
    ------
    ValueError
        Si la columna no existe o la serie no contiene ningún valor válido.
    """
    df = resultado_preprocesamiento[indice]

    if id_parcela not in df.columns:
        raise ValueError(
            f"Columna '{id_parcela}' no encontrada en el DataFrame de {indice}. "
            f"Columnas disponibles: {list(df.columns)}"
        )

    serie = df[id_parcela]
    if serie.dropna().empty:
        raise ValueError(
            f"La serie de '{id_parcela}' ({indice}) no tiene observaciones válidas."
        )

    return serie.values, serie.index


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


def _inferir_temporada(sos_fecha: pd.Timestamp | date) -> str:
    """Determina 'primera' o 'postrera' según el mes del SOS."""
    mes = sos_fecha.month if hasattr(sos_fecha, "month") else pd.Timestamp(sos_fecha).month
    return "primera" if 4 <= mes <= 7 else "postrera"


def crear_ciclo_historico(
    id_parcela: int,
    sos_fecha: pd.Timestamp | date,
    lswi_max: float | None = None,
) -> int:
    """
    Crea un registro histórico en ``produccion_acumulada_ciclo`` con
    estado ``finalizado`` y las ventanas T1/T2/T3/EOS derivadas de SOS.

    Parámetros
    ----------
    id_parcela : int
        Identificador de la parcela.
    sos_fecha : pd.Timestamp | date
        Fecha de inicio de temporada detectada.
    lswi_max : float, opcional
        Valor máximo de LSWI para la parcela (puede calcularse después).

    Retorna
    -------
    int
        ``id_ciclo`` del nuevo registro insertado.
    """
    sos = pd.Timestamp(sos_fecha).normalize()
    temporada = _inferir_temporada(sos)

    t1 = sos + timedelta(days=DIAS_VENTANAS["T1"])
    t2 = sos + timedelta(days=DIAS_VENTANAS["T2"])
    t3 = sos + timedelta(days=DIAS_VENTANAS["T3"])
    eos = sos + timedelta(days=DIAS_VENTANAS["eos"])

    sql = """
        INSERT INTO produccion_acumulada_ciclo
            (id_parcela, temporada, lswi_max, sos, t1, t2, t3, eos, estado_ciclo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'finalizado')
    """
    with closing(get_connection_raw()) as conn:
        with conn:
            cursor = conn.execute(sql, (
                id_parcela,
                temporada,
                lswi_max,
                str(sos.date()),
                str(t1.date()), str(t2.date()), str(t3.date()),
                str(eos.date()),
            ))
            return cursor.lastrowid


def persistir_sos_y_ventanas(id_ciclo: int, sos_mediana: pd.Timestamp) -> None:
    """
    Persiste SOS y fechas de ventana T1/T2/T3 en ``produccion_acumulada_ciclo``.
    Solo actualiza filas cuyo ``sos`` aún es NULL (idempotencia).
    """
    sos_mediana = pd.Timestamp(sos_mediana).normalize()
    t1 = sos_mediana + timedelta(days=DIAS_VENTANAS["T1"])
    t2 = sos_mediana + timedelta(days=DIAS_VENTANAS["T2"])
    t3 = sos_mediana + timedelta(days=DIAS_VENTANAS["T3"])

    sql = """
        UPDATE produccion_acumulada_ciclo
        SET sos = ?, t1 = ?, t2 = ?, t3 = ?
        WHERE id_ciclo = ? AND sos IS NULL;
    """
    with closing(get_connection_raw()) as conn:
        with conn:
            conn.execute(sql, (
                str(sos_mediana.date()),
                str(t1.date()), str(t2.date()), str(t3.date()),
                id_ciclo,
            ))


def detectar_y_persistir_sos_ciclo(
    ciclo: dict,
    dfs_vpm_por_parcela: dict[int, dict],
    factor_sos: float = 0.2,
) -> dict:
    """
    Detecta SOS por parcela, calcula la mediana entre parcelas con detección
    exitosa y persiste SOS + ventanas T1/T2/T3 en BD.

    Si el ciclo ya tiene ``sos`` establecido, lo retorna sin modificar BD.
    """
    if ciclo.get("sos") is not None:
        return ciclo

    id_ciclo = ciclo["id_ciclo"]
    fechas_sos: list[pd.Timestamp] = []

    for id_parcela, dfs_vpm in dfs_vpm_por_parcela.items():
        col = f"id_{id_parcela}"
        df_evi = dfs_vpm.get("EVI")
        if df_evi is None or col not in df_evi.columns:
            continue

        serie = df_evi[col]
        if serie.dropna().empty:
            continue

        try:
            resultado = detectar_sos(
                serie=serie.values,
                fechas=serie.index,
                factor=factor_sos,
            )
        except Exception:
            continue

        sos_fecha = resultado.get("sos_fecha")
        if sos_fecha is not None:
            fechas_sos.append(pd.Timestamp(sos_fecha))

    if not fechas_sos:
        return ciclo

    sos_mediana = pd.Series(sorted(fechas_sos)).median()
    persistir_sos_y_ventanas(id_ciclo, sos_mediana)

    t1 = sos_mediana + timedelta(days=DIAS_VENTANAS["T1"])
    t2 = sos_mediana + timedelta(days=DIAS_VENTANAS["T2"])
    t3 = sos_mediana + timedelta(days=DIAS_VENTANAS["T3"])

    return {
        **ciclo,
        "sos": str(sos_mediana.date()),
        "t1": str(t1.date()),
        "t2": str(t2.date()),
        "t3": str(t3.date()),
    }


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