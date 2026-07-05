import numpy as np
import pandas as pd
from scipy.optimize import curve_fit


def _doble_logistica(t, vmin, vmax, S, mS, A, mA):
    """
    Modelo doble logístico estándar para curvas fenológicas (TIMESAT). Produce una curva de subida (verdor) seguida de una
    meseta y una bajada (senescencia).

    Parámetros del modelo
    ----------------------
    vmin, vmax : nivel base (valle) y nivel de meseta (pico) del índice.
    S, mS      : punto de inflexión y tasa de la fase de subida (verdor).
    A, mA      : punto de inflexión y tasa de la fase de bajada (senescencia).
    """
    return vmin + (vmax - vmin) * (
        1 / (1 + np.exp(-mS * (t - S))) + 1 / (1 + np.exp(mA * (t - A))) - 1
    )


def ajustar_curva_doble_logistica(
    serie_suavizada: pd.Series,
    fecha_sos: pd.Timestamp,
) -> dict | None:
    """
    Ajusta una curva doble logística sobre la porción observada de una serie
    suavizada (post-Whittaker) de EVI o LSWI, expresando el tiempo como días
    transcurridos desde SOS (no fecha calendario), para capturar la forma
    fenológica del cultivo independientemente de la fecha de siembra real.

    Parámetros
    ----------
    serie_suavizada : pd.Series
        Índice suavizado (EVI o LSWI), DatetimeIndex diario, sin NaN,
        correspondiente a un solo ciclo/parcela, desde SOS hasta la última
        observación disponible.
    fecha_sos : pd.Timestamp
        Fecha de SOS detectada para este ciclo (ancla del eje t=0).

    Retorna
    -------
    dict | None
        Si el ajuste converge: {'vmin', 'vmax', 'S', 'mS', 'A', 'mA', 'r2'}.
        Si el ajuste falla (datos insuficientes o no converge): None.
        El llamador debe decidir el fallback (ej. no extender, o usar el
        último valor observado como meseta constante).
    """
    dias = (serie_suavizada.index - fecha_sos).days.to_numpy(dtype=float)
    valores = serie_suavizada.to_numpy(dtype=float)

    if len(dias) < 6:
        # Muy pocos puntos para 6 parámetros libres; el ajuste no sería confiable.
        return None

    vmin_inicial = np.percentile(valores, 5)
    vmax_inicial = np.percentile(valores, 95)
    ultimo_dia = dias[-1]

    p0 = [
        vmin_inicial,           # vmin
        vmax_inicial,           # vmax
        ultimo_dia * 0.3,       # S: inflexión de subida, estimada temprano en la serie
        0.1,                    # mS
        ultimo_dia * 0.8,       # A: inflexión de bajada, estimada tarde en la serie
        0.1,                    # mA
    ]

    limites_inferiores = [-1, -1, 0, 0.001, 0, 0.001]
    limites_superiores = [1, 1, ultimo_dia * 3, 2, ultimo_dia * 3, 2]

    try:
        params_opt, _ = curve_fit(
            _doble_logistica, dias, valores, p0=p0,
            bounds=(limites_inferiores, limites_superiores),
            maxfev=5000,
        )
    except (RuntimeError, ValueError):
        return None

    predicho = _doble_logistica(dias, *params_opt)
    ss_res = np.sum((valores - predicho) ** 2)
    ss_tot = np.sum((valores - np.mean(valores)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    nombres = ["vmin", "vmax", "S", "mS", "A", "mA"]
    resultado = dict(zip(nombres, params_opt))
    resultado["r2"] = r2
    return resultado


def extender_serie_con_curva_parametrica(
    serie_suavizada: pd.Series,
    fecha_sos: pd.Timestamp,
    fecha_fin_extension: pd.Timestamp,
    r2_minimo_aceptable: float = 0.7,
) -> tuple[pd.Series, dict | None]:
    """
    Extiende una serie suavizada (EVI/LSWI) más allá de la última observación
    real, usando una curva doble logística ajustada sobre la porción
    observada. Los valores observados se preservan sin modificación; solo
    los días posteriores a la última observación se completan con la curva.

    Parámetros
    ----------
    serie_suavizada : pd.Series
        Serie observada (post-Whittaker), hasta la fecha actual del cálculo.
    fecha_sos : pd.Timestamp
        Fecha de SOS del ciclo, ancla del eje temporal de la curva.
    fecha_fin_extension : pd.Timestamp
        Fecha hasta la cual extender (ej. EOS = SOS + 120 días).
    r2_minimo_aceptable : float
        Umbral de bondad de ajuste. Si el R² del ajuste cae por debajo de
        este valor, se considera el ajuste no confiable y no se extiende
        (se retorna la serie observada sin cambios).

    Retorna
    -------
    tuple[pd.Series, dict | None]
        (serie_extendida, parametros_ajuste)
        - serie_extendida: observado + extrapolado, DatetimeIndex diario
          continuo hasta fecha_fin_extension. Si el ajuste falla o el R² es
          insuficiente, es idéntica a serie_suavizada (sin extensión).
        - parametros_ajuste: dict de ajustar_curva_doble_logistica, o None
          si no se pudo ajustar o no cumplió el umbral de calidad. Se
          recomienda persistir este dict junto a la predicción, para
          auditoría de qué tan confiable fue la extrapolación usada.
    """
    params = ajustar_curva_doble_logistica(serie_suavizada, fecha_sos)

    if params is None or params["r2"] < r2_minimo_aceptable:
        return serie_suavizada.copy(), None

    ultima_fecha_observada = serie_suavizada.index[-1]
    fechas_futuras = pd.date_range(
        ultima_fecha_observada + pd.Timedelta(days=1),
        fecha_fin_extension,
        freq="D",
    )

    if len(fechas_futuras) == 0:
        return serie_suavizada.copy(), params

    dias_futuros = (fechas_futuras - fecha_sos).days.to_numpy(dtype=float)
    valores_futuros = _doble_logistica(
        dias_futuros, params["vmin"], params["vmax"],
        params["S"], params["mS"], params["A"], params["mA"],
    )

    serie_extrapolada = pd.Series(valores_futuros, index=fechas_futuras)
    serie_extendida = pd.concat([serie_suavizada, serie_extrapolada])

    return serie_extendida, params

def guardar_serie_extrapolada(
    id_prediccion: int,
    serie_evi_extrapolada: pd.Series | None,
    serie_lswi_extrapolada: pd.Series | None,
) -> int:
    """
    Persiste la porción proyectada (posterior a la última observación real)
    de las series de EVI/LSWI usadas para sustentar una predicción congelada
    en predicciones_ventana.

    Solo debe recibir los tramos extrapolados, no la serie completa
    observado+extrapolado — la parte observada ya vive en indices_suavizados
    y no debe duplicarse aquí.

    Parámetros
    ----------
    id_prediccion : int
        FK hacia predicciones_ventana.id_prediccion. Debe existir ya (esta
        función se llama inmediatamente después de congelar la predicción).
    serie_evi_extrapolada : pd.Series | None
        DatetimeIndex diario, valores del tramo extrapolado de EVI.
        None si el ajuste de curva para EVI no fue confiable (ver
        extender_serie_con_curva_parametrica) y no hubo extrapolación.
    serie_lswi_extrapolada : pd.Series | None
        Análogo para LSWI.

    Retorna
    -------
    int
        Número de filas escritas. 0 si ambas series son None o vacías.

    Raises
    ------
    ValueError
        Si ambas series vienen no-None pero con fechas completamente
        disjuntas (no hay ninguna fecha en común) — indicio de un error
        de las funciones de extrapolación aguas arriba, no un caso válido
        de datos parciales.
    """
    tiene_evi  = serie_evi_extrapolada  is not None and not serie_evi_extrapolada.empty
    tiene_lswi = serie_lswi_extrapolada is not None and not serie_lswi_extrapolada.empty

    if not tiene_evi and not tiene_lswi:
        print("⚠️  Sin tramo extrapolado que persistir (ninguna curva fue confiable).")
        return 0

    if tiene_evi and tiene_lswi:
        fechas = serie_evi_extrapolada.index.union(serie_lswi_extrapolada.index)
        if serie_evi_extrapolada.index.intersection(serie_lswi_extrapolada.index).empty:
            raise ValueError(
                "Las series extrapoladas de EVI y LSWI no comparten ninguna fecha; "
                "revisar las llamadas a extender_serie_con_curva_parametrica."
            )
    elif tiene_evi:
        fechas = serie_evi_extrapolada.index
    else:
        fechas = serie_lswi_extrapolada.index

    df = pd.DataFrame(index=fechas)
    df["evi_extrapolado"]  = serie_evi_extrapolada.reindex(fechas)  if tiene_evi  else None
    df["lswi_extrapolado"] = serie_lswi_extrapolada.reindex(fechas) if tiene_lswi else None

    df = df.astype(object).where(pd.notna(df), None)
    df.index = df.index.strftime("%Y-%m-%d")
    df = df.reset_index().rename(columns={"index": "fecha"})
    df.insert(0, "id_prediccion", id_prediccion)

    rows = list(df.itertuples(index=False, name=None))

    sql_upsert = """
        INSERT INTO series_extrapoladas_ventana
            (id_prediccion, fecha, evi_extrapolado, lswi_extrapolado)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (id_prediccion, fecha) DO UPDATE SET
            evi_extrapolado  = excluded.evi_extrapolado,
            lswi_extrapolado = excluded.lswi_extrapolado
    """

    with closing(get_connection_raw()) as conn:
        with conn:
            conn.executemany(sql_upsert, rows)

    print(f"✅  {len(rows)} filas escritas en 'series_extrapoladas_ventana' (id_prediccion={id_prediccion}).")
    return len(rows)



def construir_climatologia_diaria(serie_historica_agera5: pd.Series, ventana_suavizado: int = 7) -> pd.Series:
    """
    Climatología de PAR o temperatura por día del año, a partir del archivo
    histórico multi-año de AgERA5.

    Parámetros
    ----------
    serie_historica_agera5 : pd.Series
        Serie diaria histórica (varios años), DatetimeIndex.
    ventana_suavizado : int
        Ventana de promedio móvil circular sobre día-del-año, para reducir
        ruido interanual en la climatología (evita que un solo año atípico
        distorsione el valor de un día específico).

    Retorna
    -------
    pd.Series
        Índice 1-366 (día del año), valor = climatología suavizada.
    """
    dia_del_anio = serie_historica_agera5.index.dayofyear
    climatologia_cruda = serie_historica_agera5.groupby(dia_del_anio).mean()
    climatologia_cruda = climatologia_cruda.reindex(range(1, 367))

    # Promedio móvil circular (envuelve dic-enero) para suavizar
    extendida = pd.concat([climatologia_cruda, climatologia_cruda, climatologia_cruda])
    suavizada = extendida.rolling(ventana_suavizado, center=True, min_periods=1).mean()
    return suavizada.iloc[366:732].reset_index(drop=True)

def construir_serie_climatica_prediccion(
    fecha_inicio_ventana: pd.Timestamp,
    fecha_fin_prediccion: pd.Timestamp,  # ej. EOS = SOS + 120
    serie_real_disponible: pd.Series,     # AgERA5 real, hasta donde llegue (rezago de ~8 días incluido)
    climatologia: pd.Series,              # salida de construir_climatologia_diaria
    ) -> pd.Series:
    """
    Combina observación real (donde exista) con climatología (donde no),
    para PAR o Tscalar, sobre toda la ventana necesaria para la cadena
    GPP→NPP→rendimiento de una ventana predictiva T1/T2/T3.
    """
    fechas = pd.date_range(fecha_inicio_ventana, fecha_fin_prediccion, freq="D")
    serie = pd.Series(index=fechas, dtype=float)

    real = serie_real_disponible.reindex(fechas)
    serie.loc[real.notna()] = real[real.notna()]

    faltantes = serie.isna()
    dias_anio_faltantes = fechas[faltantes].dayofyear
    serie.loc[faltantes] = climatologia.reindex(dias_anio_faltantes).to_numpy()

    return serie

def obtener_climatologia(variable: str, id_region: int = 1) -> pd.Series:
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(
            "SELECT dia_anio, valor_climatologico FROM climatologia_diaria "
            "WHERE variable = ? AND id_region = ? ORDER BY dia_anio",
            conn, params=(variable, id_region),
        )
    return df.set_index("dia_anio")["valor_climatologico"]

def guardar_climatologia_diaria(
    climatologia_par: pd.Series,
    climatologia_temperatura: pd.Series,
    anio_min_incluido: int,
    anio_max_incluido: int,
    id_region: int = 1,
) -> int:
    """
    Persiste (upsert) la climatología diaria de PAR y temperatura en
    climatologia_diaria. Se ejecuta una vez por año, típicamente antes
    del inicio de cada temporada de siembra (primera/postrera), tras
    incorporar el año calendario más reciente ya completo del archivo
    histórico de AgERA5.

    Parámetros
    ----------
    climatologia_par, climatologia_temperatura : pd.Series
        Índice 1-366 (día del año), salida de construir_climatologia_diaria.
    anio_min_incluido, anio_max_incluido : int
        Rango de años del archivo histórico usado para calcular la
        climatología, para trazabilidad.
    id_region : int
        Identificador de celda de grid AgERA5 (default 1, dado que el
        área de estudio cae en una sola celda de ~11 km).

    Retorna
    -------
    int
        Número de filas escritas (2 x 366, una por variable y día).
    """
    filas = []
    for variable, serie in (("PAR", climatologia_par), ("temperatura", climatologia_temperatura)):
        for dia_anio, valor in serie.items():
            filas.append((id_region, variable, int(dia_anio), float(valor),
                          anio_min_incluido, anio_max_incluido))

    sql_upsert = """
        INSERT INTO climatologia_diaria
            (id_region, variable, dia_anio, valor_climatologico,
             anio_min_incluido, anio_max_incluido)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (id_region, variable, dia_anio) DO UPDATE SET
            valor_climatologico = excluded.valor_climatologico,
            anio_min_incluido   = excluded.anio_min_incluido,
            anio_max_incluido   = excluded.anio_max_incluido,
            fecha_calculo       = CURRENT_TIMESTAMP
    """

    with closing(get_connection_raw()) as conn:
        with conn:
            conn.executemany(sql_upsert, filas)

    print(f"✅ Climatología actualizada: {len(filas)} filas (años {anio_min_incluido}-{anio_max_incluido}).")
    return len(filas)

#=================================================================================================
#                                      Experimental
#=================================================================================================
def construir_ensamble_climatico(
    fecha_inicio_ventana: pd.Timestamp,
    fecha_fin_prediccion: pd.Timestamp,
    serie_real_disponible: pd.Series,
    archivo_historico_agera5: pd.DataFrame,  # columnas: un año por columna, índice día-del-año
) -> dict[int, pd.Series]:
    """
    Para cada año histórico disponible, construye una trayectoria climática
    alternativa: observación real donde exista, y el clima de ESE año
    específico (no el promedio) para los días faltantes.

    Retorna un dict {anio: serie_climatica}, que alimentado a la cadena
    GPP→NPP→rendimiento produce una distribución de rendimientos estimados
    en vez de un único valor puntual.
    """
    trayectorias = {}
    fechas = pd.date_range(fecha_inicio_ventana, fecha_fin_prediccion, freq="D")

    for anio in archivo_historico_agera5.columns:
        serie = pd.Series(index=fechas, dtype=float)
        real = serie_real_disponible.reindex(fechas)
        serie.loc[real.notna()] = real[real.notna()]

        faltantes = serie.isna()
        dias_anio_faltantes = fechas[faltantes].dayofyear
        serie.loc[faltantes] = archivo_historico_agera5[anio].reindex(dias_anio_faltantes).to_numpy()

        trayectorias[anio] = serie

    return trayectorias

def reconstruir_serie_proyectada(id_prediccion: int, id_ciclo: int) -> pd.DataFrame:
    """
    Reconstruye la serie completa (observado + extrapolado) que sustentó
    una predicción específica, para comparar contra lo que realmente
    ocurrió después.
    """
    with closing(get_connection_raw()) as conn:
        observado = pd.read_sql(
            "SELECT fecha, evi, lswi FROM indices_suavizados WHERE id_ciclo = ?",
            conn, params=(id_ciclo,),
        )
        extrapolado = pd.read_sql(
            "SELECT fecha, evi_extrapolado AS evi, lswi_extrapolado AS lswi "
            "FROM series_extrapoladas_ventana WHERE id_prediccion = ?",
            conn, params=(id_prediccion,),
        )

    observado["origen"] = "observado"
    extrapolado["origen"] = "extrapolado"
    return pd.concat([observado, extrapolado]).sort_values("fecha").reset_index(drop=True)