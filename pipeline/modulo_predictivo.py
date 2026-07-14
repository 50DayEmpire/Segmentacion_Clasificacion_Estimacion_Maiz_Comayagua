import logging
import numpy as np
import pandas as pd
from contextlib import closing
from datetime import date, datetime, timedelta
from scipy.optimize import curve_fit, least_squares

from config import DURACION_CICLO
from utils.conexionDB import get_connection_raw

_log_pred = logging.getLogger("seed_historico_offline")


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


def _doble_logistica_t1(t, vmin, vmax, S, mS):
    A_fijo = DURACION_CICLO * 0.7
    return vmin + (vmax - vmin) * (
        1 / (1 + np.exp(-mS * (t - S))) + 1 / (1 + np.exp(0.85 * mS * (t - A_fijo))) - 1
    )


def _residuales_t1(params, dias, valores):
    vmin, vmax, S, mS = params
    predicho = _doble_logistica_t1(dias, vmin, vmax, S, mS)
    residuales_valor = predicho - valores

    n_obs = len(dias)
    n_ancla = min(5, n_obs)
    t_U = dias[-1]

    pend_emp = np.polyfit(dias[-n_ancla:], valores[-n_ancla:], 1)[0]

    h = 0.5
    f_mas  = _doble_logistica_t1(np.array([t_U + h]), vmin, vmax, S, mS)[0]
    f_menos = _doble_logistica_t1(np.array([t_U - h]), vmin, vmax, S, mS)[0]
    pend_mod = (f_mas - f_menos) / (2 * h)

    curv_emp = np.polyfit(dias[-n_ancla:], valores[-n_ancla:], 2)[0] * 2

    f_0  = _doble_logistica_t1(np.array([t_U]),       vmin, vmax, S, mS)[0]
    curv_mod = (f_mas - 2 * f_0 + f_menos) / (h ** 2)

    peso_pendiente = n_obs * 0.5
    peso_curvatura = 5.0

    residual_pend = peso_pendiente * (pend_mod - pend_emp)
    residual_curv = peso_curvatura * (curv_mod - curv_emp)

    return np.concatenate([residuales_valor, [residual_pend], [residual_curv]])


def _residuales_t3(params, dias, valores):
    vmin, vmax, S, mS, A, mA = params
    predicho = _doble_logistica(dias, vmin, vmax, S, mS, A, mA)
    residuales_valor = predicho - valores

    n_obs = len(dias)
    n_ancla = min(5, n_obs)
    t_U = dias[-1]

    pend_emp = np.polyfit(dias[-n_ancla:], valores[-n_ancla:], 1)[0]

    h = 0.5
    f_mas  = _doble_logistica(np.array([t_U + h]), vmin, vmax, S, mS, A, mA)[0]
    f_menos = _doble_logistica(np.array([t_U - h]), vmin, vmax, S, mS, A, mA)[0]
    pend_mod = (f_mas - f_menos) / (2 * h)

    curv_emp = np.polyfit(dias[-n_ancla:], valores[-n_ancla:], 2)[0] * 2

    f_0  = _doble_logistica(np.array([t_U]),       vmin, vmax, S, mS, A, mA)[0]
    curv_mod = (f_mas - 2 * f_0 + f_menos) / (h ** 2)

    peso_pendiente = n_obs * 0.35
    peso_curvatura = 5.0

    residual_pend = peso_pendiente * (pend_mod - pend_emp)
    residual_curv = peso_curvatura * (curv_mod - curv_emp)

    return np.concatenate([residuales_valor, [residual_pend], [residual_curv]])


def ajustar_curva_doble_logistica(
    serie_suavizada: pd.Series,
    fecha_sos: pd.Timestamp,
    duracion_ciclo_dias: int | None = None,
    ventana: str = "T3",
    valor_valle: float | None = None,
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
    duracion_ciclo_dias : int | None
        Duración total esperada del ciclo (SOS a EOS) en días. Si se
        proporciona, se usa para acotar los parámetros S y A a la escala
        fenológica real; si es None, se usan cotas basadas en el último
        día observado (fallback para compatibilidad).
    ventana : str
        Ventana de predicción (T1, T2, T3). T1 usa parámetros congelados
        (vmax y A fijos, mA=mS) para estabilizar el ajuste con pocos datos.
        Las tres ventanas usan `least_squares` con residuos personalizados
        que penalizan desviaciones de pendiente y curvatura al final del
        tramo observado, para evitar divergencias en la extrapolación.

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
        return None

    vmin_inicial = np.percentile(valores, 5)
    ultimo_dia = dias[-1]

    if valor_valle is not None:
        vmin_lb = valor_valle - 0.02
        vmin_ub = valor_valle + 0.03
        vmin_inicial = np.clip(vmin_inicial, vmin_lb, vmin_ub)
    else:
        vmin_lb = -1
        vmin_ub = 1

    if ventana == "T1":
        max_obs = float(valores.max())
        vmax_lb = max_obs
        vmax_ub = max(max_obs * 1.3, max_obs + 0.1)
        p0_vmax = min(max(max_obs * 1.15, vmax_lb), vmax_ub)
        p0 = [vmin_inicial, p0_vmax, DURACION_CICLO * 0.2, 0.1]
        limites_inf = [vmin_lb, vmax_lb,    0,           0.001]
        limites_sup = [vmin_ub,  vmax_ub,  36,          2    ]

        resultado = least_squares(
            _residuales_t1, x0=p0, bounds=(limites_inf, limites_sup),
            args=(dias, valores), max_nfev=5000,
        )
        if not resultado.success:
            return None
        params_opt = resultado.x

        predicho = _doble_logistica_t1(dias, *params_opt)
        vmin_o, vmax_o, S_o, mS_o = params_opt
        params_opt_full = [vmin_o, vmax_o, S_o, mS_o, DURACION_CICLO * 0.7, mS_o * 0.85]
    elif ventana == "T2":
        vmax_inicial = np.percentile(valores, 95)
        ref_ciclo = duracion_ciclo_dias if duracion_ciclo_dias else ultimo_dia

        p0 = [vmin_inicial, vmax_inicial, ref_ciclo * 0.2, 0.1]
        s_sup = min(ultimo_dia, ref_ciclo / 2) if duracion_ciclo_dias else ultimo_dia * 3
        limites_inf = [vmin_lb, -1, 0, 0.001]
        limites_sup = [vmin_ub,  1,  s_sup, 2]

        resultado = least_squares(
            _residuales_t1, x0=p0, bounds=(limites_inf, limites_sup),
            args=(dias, valores), max_nfev=5000,
        )
        if not resultado.success:
            return None
        params_opt = resultado.x

        predicho = _doble_logistica_t1(dias, *params_opt)
        vmin_o, vmax_o, S_o, mS_o = params_opt
        params_opt_full = [vmin_o, vmax_o, S_o, mS_o, DURACION_CICLO * 0.7, mS_o * 0.85]
    else:
        vmax_inicial = np.percentile(valores, 95)
        ref_ciclo = duracion_ciclo_dias if duracion_ciclo_dias else ultimo_dia

        p0 = [
            vmin_inicial, vmax_inicial,
            ref_ciclo * 0.2, 0.1,
            ref_ciclo * 0.7, 0.15,
        ]
        s_sup = min(ultimo_dia, ref_ciclo / 2) if duracion_ciclo_dias else ultimo_dia * 3
        a_sup = int(ref_ciclo * 1.1) if duracion_ciclo_dias else ultimo_dia * 3
        limites_inf = [vmin_lb, -1, 0, 0.001, 0, 0.12]
        limites_sup = [vmin_ub, 1,  s_sup, 2, a_sup, 2]

        resultado = least_squares(
            _residuales_t3, x0=p0, bounds=(limites_inf, limites_sup),
            args=(dias, valores), max_nfev=5000,
        )
        if not resultado.success:
            return None
        params_opt = resultado.x

        predicho = _doble_logistica(dias, *params_opt)
        params_opt_full = list(params_opt)

    ss_res = np.sum((valores - predicho) ** 2)
    ss_tot = np.sum((valores - np.mean(valores)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    nombres = ["vmin", "vmax", "S", "mS", "A", "mA"]
    resultado = dict(zip(nombres, params_opt_full))
    resultado["r2"] = r2
    return resultado


def extender_serie_con_curva_parametrica(
    serie_suavizada: pd.Series,
    fecha_sos: pd.Timestamp,
    fecha_fin_extension: pd.Timestamp,
    r2_minimo_aceptable: float = 0.7,
    ventana: str = "T3",
    valor_valle: float | None = None,
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
    ventana : str
        Ventana de predicción (T1, T2, T3). Se delega a
        ajustar_curva_doble_logistica para aplicar estrategia de ajuste
        diferenciada por ventana.

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
    n_obs = len(serie_suavizada)
    duracion_ciclo_dias = (fecha_fin_extension - fecha_sos).days if fecha_fin_extension else None
    params = ajustar_curva_doble_logistica(
        serie_suavizada, fecha_sos, duracion_ciclo_dias=duracion_ciclo_dias,
        ventana=ventana, valor_valle=valor_valle,
    )

    if params is None or params["r2"] < r2_minimo_aceptable:
        _log_pred.warning(
            "[CURVA] NO se extiende — n_obs=%d R²=%s (None si falló ajuste)",
            n_obs, params["r2"] if params else "None",
        )
        return serie_suavizada.copy(), None

    ultima_fecha_observada = serie_suavizada.index[-1]
    fechas_futuras = pd.date_range(
        ultima_fecha_observada + pd.Timedelta(days=1),
        fecha_fin_extension,
        freq="D",
    )

    _log_pred.debug(
        "[CURVA] OK — n_obs=%d R²=%.4f vmin=%.3f vmax=%.3f S=%.1f mS=%.4f A=%.1f mA=%.4f "
        "ult_obs=%s ext_hasta=%s (%d días futuros)",
        n_obs, params["r2"], params["vmin"], params["vmax"],
        params["S"], params["mS"], params["A"], params["mA"],
        ultima_fecha_observada.date(), fecha_fin_extension.date(), len(fechas_futuras),
    )

    if len(fechas_futuras) == 0:
        return serie_suavizada.copy(), params

    dias_futuros = (fechas_futuras - fecha_sos).days.to_numpy(dtype=float)
    valores_futuros = _doble_logistica(
        dias_futuros, params["vmin"], params["vmax"],
        params["S"], params["mS"], params["A"], params["mA"],
    )

    if ventana == "T1":
        # Constante de tiempo para el decaimiento (en días)
        tau = 10.0
        ultimo_dia_obs = float((ultima_fecha_observada - fecha_sos).days)
        
        # Evaluar el modelo doble logístico puro en el último día observado t_U
        valor_modelo_t_U = _doble_logistica(
            np.array([ultimo_dia_obs]), params["vmin"], params["vmax"],
            params["S"], params["mS"], params["A"], params["mA"],
        )[0]
        
        # Desfase vertical en t_U
        offset = float(serie_suavizada.iloc[-1] - valor_modelo_t_U)
        
        # Aplicar el offset decay exponencial a los días futuros
        valores_futuros = valores_futuros + offset * np.exp(-(dias_futuros - ultimo_dia_obs) / tau)

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
    return suavizada.iloc[366:732].set_axis(range(1, 367))

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
    for variable, serie in (("radiacion", climatologia_par), ("temperatura", climatologia_temperatura)):
        for dia_anio, valor in serie.items():
            filas.append((id_region, variable, int(dia_anio), float(valor),
                          int(anio_min_incluido), int(anio_max_incluido)))

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


def climatologia_disponible() -> bool:
    """True si ``climatologia_diaria`` tiene al menos una fila."""
    try:
        with closing(get_connection_raw()) as conn:
            n = conn.execute("SELECT COUNT(*) FROM climatologia_diaria;").fetchone()[0]
        return n > 0
    except Exception:
        return False


def existe_prediccion_ventana(id_ciclo: int, ventana: str) -> bool:
    """True si ya existe una predicción congelada para (id_ciclo, ventana)."""
    sql = "SELECT 1 FROM predicciones_ventana WHERE id_ciclo=? AND ventana=? LIMIT 1;"
    try:
        with closing(get_connection_raw()) as conn:
            row = conn.execute(sql, (id_ciclo, ventana)).fetchone()
        return row is not None
    except Exception:
        return False


def prediccion_congelada_antes_de(
    id_ciclo: int,
    ventana: str,
    fecha_limite: date | str,
) -> bool:
    """
    True si existe predicción con ``fecha_congelamiento`` anterior o igual a
    ``fecha_limite`` (modo simulación — predicciones inmutables).
    """
    sql = """
        SELECT 1 FROM predicciones_ventana
        WHERE id_ciclo=? AND ventana=?
          AND DATE(fecha_congelamiento) <= ?
        LIMIT 1;
    """
    try:
        with closing(get_connection_raw()) as conn:
            row = conn.execute(
                sql, (id_ciclo, ventana, str(fecha_limite)),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _obtener_area_ha(id_parcela: int) -> float | None:
    try:
        with closing(get_connection_raw()) as conn:
            row = conn.execute(
                "SELECT area_ha FROM parcelas_vigentes WHERE id_parcela=?;",
                (id_parcela,),
            ).fetchone()
        return float(row[0]) if row else None
    except Exception:
        return None


def ejecutar_prediccion_ventana(
    ciclo: dict,
    ventana: str,
    fecha_ventana: date,
    dfs_vpm_por_parcela: dict[int, dict],
    fecha_hoy: date,
    persistir: bool = True,
) -> dict | None:
    """
    Ejecuta el flujo VPM completo para una ventana T1/T2/T3 de un ciclo.

    Si ``persistir=True`` (defecto), persiste el resultado en
    ``predicciones_ventana`` y el tramo extrapolado en
    ``series_extrapoladas_ventana``. Si ``persistir=False``
    solo retorna el dict con métricas sin escribir en BD.

    Retorna un dict con métricas de la predicción si fue exitosa, o None.
    """
    from pipeline.ingesta import cargar_clima_desde_bd
    from pipeline.modulo_vpm import calcular_biomasa_y_rendimiento, calcular_gpp_vpm

    id_ciclo   = ciclo["id_ciclo"]
    id_parcela = ciclo["id_parcela"]
    fecha_inicio_str = str(ciclo.get("fecha_inicio", ""))
    sos_str    = ciclo.get("sos")
    lswi_max   = ciclo.get("lswi_max")

    if not sos_str:
        return None

    if not climatologia_disponible():
        raise RuntimeError(
            f"Climatología no disponible para ventana {ventana} del ciclo id_ciclo={id_ciclo}"
        )

    sos_ts = pd.Timestamp(sos_str)

    if ventana == "EOS":
        eos_str = ciclo.get("eos")
        if not eos_str:
            return None
        eos_ts = pd.Timestamp(eos_str)
    else:
        eos_ts = sos_ts + timedelta(days=DURACION_CICLO)

    clim_rad = obtener_climatologia("radiacion")
    clim_temp = obtener_climatologia("temperatura")

    dfs_vpm = dfs_vpm_por_parcela.get(id_parcela)
    if dfs_vpm is None:
        return None

    col = f"id_{id_parcela}"

    serie_evi_obs  = (
        dfs_vpm["EVI"][col].dropna()
        if col in dfs_vpm["EVI"].columns else pd.Series(dtype=float)
    )
    serie_lswi_obs = (
        dfs_vpm["LSWI"][col].dropna()
        if col in dfs_vpm["LSWI"].columns else pd.Series(dtype=float)
    )

    _log_pred.debug(
        "[VPM] ciclo=%d parcela=%d ventana=%s fecha_ventana=%s sos=%s eos=%s "
        "obs_evi=%d días ult_evi=%s obs_lswi=%d días fecha_hoy=%s",
        id_ciclo, id_parcela, ventana, fecha_ventana, sos_ts.date(), eos_ts.date(),
        len(serie_evi_obs),
        serie_evi_obs.index[-1].date() if not serie_evi_obs.empty else "N/A",
        len(serie_lswi_obs),
        fecha_hoy,
    )

    valor_valle_evi = ciclo.get("valor_valle_evi")
    valor_valle_lswi = ciclo.get("valor_valle_lswi")

    if ventana == "EOS":
        serie_evi_ext  = serie_evi_obs
        serie_lswi_ext = serie_lswi_obs
    else:
        serie_evi_ext,  _ = extender_serie_con_curva_parametrica(
            serie_evi_obs, sos_ts, eos_ts,
            ventana=ventana, valor_valle=valor_valle_evi,
        )
        serie_lswi_ext, _ = extender_serie_con_curva_parametrica(
            serie_lswi_obs, sos_ts, eos_ts,
            ventana=ventana, valor_valle=valor_valle_lswi,
        )

    df_evi_ext  = pd.DataFrame({col: serie_evi_ext})
    df_lswi_ext = pd.DataFrame({col: serie_lswi_ext})

    lswi_max_serie = float(serie_lswi_ext.max()) if not serie_lswi_ext.empty else None
    if lswi_max is not None and lswi_max_serie is not None:
        lswi_max_usado = max(float(lswi_max), lswi_max_serie)
    else:
        lswi_max_usado = float(lswi_max) if lswi_max is not None else lswi_max_serie

    df_w_scalar = (
        (1.0 + df_lswi_ext) / (1.0 + lswi_max_usado)
        if lswi_max_usado else df_lswi_ext.copy()
    )
    df_fpar = 1.0 * df_evi_ext

    dfs_veg_ext = {
        "EVI":      df_evi_ext,
        "LSWI":     df_lswi_ext,
        "FPAR":     df_fpar,
        "W_scalar": df_w_scalar,
    }

    _log_pred.debug(
        "[VPM_IN] ciclo=%d ventana=%s evi_obs=%d evi_ext=%d lswi_ext=%d "
        "lswi_max_usado=%.3f W_scalar_medio=%.3f W_scalar_max=%.3f",
        id_ciclo, ventana,
        len(serie_evi_obs), len(serie_evi_ext), len(serie_lswi_ext),
        lswi_max_usado if lswi_max_usado else 0,
        df_w_scalar[col].mean() if col in df_w_scalar.columns else 0,
        df_w_scalar[col].max() if col in df_w_scalar.columns else 0,
    )

    try:
        dfs_clima = cargar_clima_desde_bd(
            fecha_inicio=fecha_inicio_str,
            fecha_fin=str(fecha_hoy),
            ids_parcelas=[id_parcela],
        )
        col_clima = col
        serie_temp_real = dfs_clima["temperature-mean"][col_clima].dropna()
        serie_rad_real  = dfs_clima["solar-radiation-flux"][col_clima].dropna()
    except Exception:
        serie_temp_real = pd.Series(dtype=float)
        serie_rad_real  = pd.Series(dtype=float)

    fechas_ext = serie_evi_ext.index
    serie_temp_completa = construir_serie_climatica_prediccion(
        pd.Timestamp(fecha_inicio_str), eos_ts, serie_temp_real, clim_temp,
    ).reindex(fechas_ext)
    serie_rad_completa = construir_serie_climatica_prediccion(
        pd.Timestamp(fecha_inicio_str), eos_ts, serie_rad_real, clim_rad,
    ).reindex(fechas_ext)

    dfs_clima_ext = {
        "temperature-mean":     pd.DataFrame({col: serie_temp_completa}),
        "solar-radiation-flux": pd.DataFrame({col: serie_rad_completa}),
    }

    _log_pred.debug(
        "[CLIMA] ciclo=%d ventana=%s temp_real=%d días rad_real=%d días "
        "clima_ext desde=%s hasta=%s",
        id_ciclo, ventana,
        len(serie_temp_real), len(serie_rad_real),
        serie_temp_completa.index.min().date() if not serie_temp_completa.empty else "N/A",
        serie_temp_completa.index.max().date() if not serie_temp_completa.empty else "N/A",
    )

    dfs_gpp = calcular_gpp_vpm(dfs_vegetacion=dfs_veg_ext, dfs_clima=dfs_clima_ext)

    try:
        from pipeline.ingesta import guardar_gpp_diario
        guardar_gpp_diario(dfs_gpp)
    except Exception as exc:
        _log_pred.warning("[GPP] No se pudo persistir GPP diario: %s", exc)

    df_gpp_recortado = dfs_gpp["GPP"].loc[sos_ts:eos_ts]

    _log_pred.debug(
        "[GPP] ciclo=%d ventana=%s GPP_medio=%.3f GPP_max=%.3f GPP_sum=%.1f "
        "dias_gpp=%d rango=%s a %s",
        id_ciclo, ventana,
        dfs_gpp["GPP"][col].mean(), dfs_gpp["GPP"][col].max(),
        dfs_gpp["GPP"][col].sum(),
        len(df_gpp_recortado),
        df_gpp_recortado.index.min().date() if not df_gpp_recortado.empty else "N/A",
        df_gpp_recortado.index.max().date() if not df_gpp_recortado.empty else "N/A",
    )

    resultado_rend   = calcular_biomasa_y_rendimiento(df_gpp_recortado)

    yield_tha       = float(resultado_rend["yield_final_tha"].iloc[0])
    yield_qq_ha     = yield_tha * 22.0458
    gpp_acumulado   = float(dfs_gpp["GPP"][col].sum())
    npp_acumulado   = float(resultado_rend["npp_diario"][col].sum())

    area_ha = _obtener_area_ha(id_parcela)
    yield_qq_parcela = yield_qq_ha * area_ha if area_ha else None

    id_prediccion = None
    if persistir:
        sql_ins = """
            INSERT INTO predicciones_ventana
                (id_ciclo, id_parcela, ventana, fecha_ventana,
                 lswi_max_efectivo_usado, gpp_acumulado, npp_acumulado,
                 rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela,
                 fecha_congelamiento)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (id_ciclo, ventana) DO NOTHING;
        """
        with closing(get_connection_raw()) as conn:
            with conn:
                cur = conn.execute(sql_ins, (
                    id_ciclo, id_parcela, ventana, str(fecha_ventana),
                    lswi_max_usado, gpp_acumulado, npp_acumulado,
                    yield_qq_ha, yield_qq_parcela,
                ))
                if cur.rowcount > 0:
                    id_prediccion = cur.lastrowid

        if id_prediccion is not None:
            ult_obs_evi  = serie_evi_obs.index[-1]  if not serie_evi_obs.empty  else None
            ult_obs_lswi = serie_lswi_obs.index[-1] if not serie_lswi_obs.empty else None

            tramo_evi  = (
                serie_evi_ext.loc[serie_evi_ext.index > ult_obs_evi]
                if ult_obs_evi else None
            )
            tramo_lswi = (
                serie_lswi_ext.loc[serie_lswi_ext.index > ult_obs_lswi]
                if ult_obs_lswi else None
            )
            guardar_serie_extrapolada(id_prediccion, tramo_evi, tramo_lswi)

    return {
        "id_ciclo": id_ciclo,
        "ventana": ventana,
        "yield_qq_ha": yield_qq_ha,
        "yield_qq_parcela": yield_qq_parcela,
        "gpp_acumulado": gpp_acumulado,
        "npp_acumulado": npp_acumulado,
        "fecha_congelamiento": datetime.utcnow().isoformat(),
        "parcelas_ok": 1 if id_prediccion is not None else 0,
    }


#=================================================================================================
#                                      Experimental
#=================================================================================================
def construir_serie_con_anclas_climatologicas(
    serie_observada: pd.Series,
    fecha_sos: pd.Timestamp,
    fecha_fin_extension: pd.Timestamp,
    curva_climatologica: pd.Series,  # indexada por días-desde-SOS, mismo tipo de ciclo
    peso_ancla: float = 0.3,
) -> tuple[pd.Series, pd.Series]:
    """
    Extiende la serie observada con pseudo-observaciones derivadas de la
    curva climatológica de ciclos previos del mismo tipo, para que
    Whittaker las use como guía de forma en el tramo no observado, en
    vez de asumir una forma paramétrica fija (doble logística).

    Retorna
    -------
    (serie_extendida_con_nan, pesos)
        serie_extendida_con_nan: observado + anclas climatológicas,
        con NaN en los días intermedios que Whittaker debe rellenar.
        pesos: mismo índice, 1.0 en observado, peso_ancla en anclas, 0 en NaN.
    """
    dias_futuros = pd.date_range(
        serie_observada.index[-1] + pd.Timedelta(days=1),
        fecha_fin_extension, freq="D",
    )
    dias_desde_sos_futuros = (dias_futuros - fecha_sos).days

    # Solo anclar cada N días (ej. cada 5), no todos los días futuros,
    # para que Whittaker tenga libertad de interpolar suavemente entre anclas
    indices_ancla = range(0, len(dias_futuros), 5)
    valores_ancla = curva_climatologica.reindex(dias_desde_sos_futuros).to_numpy()

    valores_futuros = np.full(len(dias_futuros), np.nan)
    pesos_futuros = np.zeros(len(dias_futuros))
    for i in indices_ancla:
        if not np.isnan(valores_ancla[i]):
            valores_futuros[i] = valores_ancla[i]
            pesos_futuros[i] = peso_ancla

    serie_futura = pd.Series(valores_futuros, index=dias_futuros)
    serie_extendida = pd.concat([serie_observada, serie_futura])

    pesos_observado = pd.Series(1.0, index=serie_observada.index)
    pesos = pd.concat([pesos_observado, pd.Series(pesos_futuros, index=dias_futuros)])

    return serie_extendida, pesos


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