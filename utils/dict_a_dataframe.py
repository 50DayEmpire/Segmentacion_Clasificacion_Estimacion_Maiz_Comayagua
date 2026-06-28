# ==========================================
# CELDA: UTILIDAD — openeo_dict_to_dataframes
# (Ejecutar una vez, reutilizar en todo el notebook)
# ==========================================
import pandas as pd
import numpy as np

def openeo_dict_to_dataframes(
    diccionario: dict,
    nombres_bandas: list,
    nombres_columnas: list = None,
    transformaciones: dict = None
) -> dict:
    """
    Convierte el dict retornado por openEO aggregate_spatial().execute()
    en un dict de DataFrames pandas, uno por banda.

    Parameters
    ----------
    diccionario : dict
        Salida directa de cube.execute(). Claves = fechas ISO, valores = lista
        de geometrías × bandas: [[b0_g0, b1_g0], [b0_g1, b1_g1], ...].
    nombres_bandas : list[str]
        Nombres de las bandas en el orden posicional que retorna openEO.
        Ejemplo: ["EVI", "LSWI"] o ["temperature-mean", "solar-radiation-flux"].
    nombres_columnas : list[str], opcional
        Etiquetas para las columnas (geometrías). Si None, genera "Parcela_1", ...
    transformaciones : dict, opcional
        {nombre_banda: callable} para transformar valores crudos antes de
        almacenarlos. Útil para conversiones de escala.
        Ejemplo: {"temperature-mean": lambda x: x / 100.0 - 273.15}

    Returns
    -------
    dict[str, pd.DataFrame]
        Clave = nombre de banda. Valor = DataFrame con DatetimeIndex
        normalizado (sin zona horaria, truncado a medianoche).
        Todos los valores son float64; None → np.nan.

    Raises
    ------
    ValueError
        Si el número de bandas detectado no coincide con nombres_bandas.
    """
    if transformaciones is None:
        transformaciones = {}

    # --- 1. Ordenar fechas y convertir a DatetimeIndex normalizado ---
    fechas_str = sorted(diccionario.keys())
    fechas_idx = pd.to_datetime(fechas_str).tz_localize(None).normalize()

    # --- 2. Detectar número de geometrías en la primera fecha válida ---
    num_geom = None
    for f in fechas_str:
        muestra = diccionario[f]
        if muestra is not None and len(muestra) > 0:
            num_geom = len(muestra)
            break
    if num_geom is None:
        raise ValueError("El diccionario no contiene fechas con datos válidos.")

    # Validar consistencia con el número de bandas esperado
    primer_elemento = diccionario[fechas_str[0]][0] if diccionario[fechas_str[0]] else []
    if isinstance(primer_elemento, list) and len(primer_elemento) != len(nombres_bandas):
        raise ValueError(
            f"Se esperaban {len(nombres_bandas)} bandas ({nombres_bandas}), "
            f"pero openEO retornó {len(primer_elemento)} por geometría."
        )

    # --- 3. Columnas de geometrías ---
    if nombres_columnas is None:
        nombres_columnas = [f"Parcela_{i+1}" for i in range(num_geom)]

    # --- 4. Acumular listas por banda ---
    # Estructura: {nombre_banda: [[val_geom0_fecha0, val_geom1_fecha0, ...], ...]}
    acumuladores = {banda: [] for banda in nombres_bandas}

    for f in fechas_str:
        valores_fecha = diccionario[f]  # Lista de num_geom elementos

        for idx_banda, nombre_banda in enumerate(nombres_bandas):
            transform = transformaciones.get(nombre_banda, None)
            fila = []

            for geom in valores_fecha:
                if geom is None:
                    fila.append(np.nan)
                    continue

                # Soporte para elemento escalar o lista de bandas
                val_raw = geom[idx_banda] if isinstance(geom, (list, tuple)) else geom

                if val_raw is None or (isinstance(val_raw, float) and np.isnan(val_raw)):
                    fila.append(np.nan)
                elif transform is not None:
                    fila.append(float(transform(val_raw)))
                else:
                    fila.append(float(val_raw))

            acumuladores[nombre_banda].append(fila)

    # --- 5. Construir DataFrames ---
    resultado = {}
    for nombre_banda in nombres_bandas:
        df = pd.DataFrame(
            acumuladores[nombre_banda],
            index=fechas_idx,
            columns=nombres_columnas,
            dtype=float
        )
        resultado[nombre_banda] = df

    return resultado