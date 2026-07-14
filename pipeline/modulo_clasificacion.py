from scipy.stats import pearsonr
import pandas as pd
import numpy as np

def cargar_patron_desde_bd(conn, subtipo, version=None):
    query = """
        SELECT dia_post_sos, evi_promedio
        FROM patron_referencia_fenologico
        WHERE subtipo = ?
        {}
        ORDER BY dia_post_sos
    """.format("AND version = ?" if version else
               "AND version = (SELECT MAX(version) FROM patron_referencia_fenologico WHERE subtipo = ?)")
    params = (subtipo, version) if version else (subtipo, subtipo)
    df = pd.read_sql(query, conn, params=params)
    return df["evi_promedio"].values

def persistir_clasificacion_v2(conn, resultado, id_ciclo, umbral_maiz=70, umbral_no_maiz=30):
    if resultado["estado"] != "evaluado":
        return  # no hay nada que persistir si está fuera de ventana o sin patrón

    score = resultado["score_compuesto"]
    if pd.isna(score):
        cultivo_predicho = "incierto"
    elif score >= umbral_maiz:
        cultivo_predicho = "maiz"
    elif score <= umbral_no_maiz:
        cultivo_predicho = "no_maiz"
    else:
        cultivo_predicho = "incierto"

    dia = resultado["dia_post_sos"]
    ventana = "T1" if dia <= 30 else ("T2" if dia <= 60 else "T3")

    with conn:
        conn.execute(
            """
            INSERT INTO clasificacion_cultivo_ciclo
                (id_ciclo, id_parcela, ventana, dia_post_sos, patron_usado,
                 score_forma_pearson, score_magnitud_pendiente, score_compuesto, cultivo_predicho)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id_ciclo, ventana) DO UPDATE SET
                score_compuesto = excluded.score_compuesto,
                cultivo_predicho = excluded.cultivo_predicho,
                score_forma_pearson = excluded.score_forma_pearson,
                score_magnitud_pendiente = excluded.score_magnitud_pendiente,
                fecha_calculo = CURRENT_TIMESTAMP
            """,
            (id_ciclo, resultado["id_parcela"], ventana, dia, resultado["patron_usado"],
             resultado["r_forma"], resultado["pendiente_obs"], score, cultivo_predicho),
        )

def correlacion_truncada(id_parcela, sos, t_actual, mu_ref, df_evi):
    col = f"id_{id_parcela}"
    rango = pd.date_range(start=sos, periods=t_actual + 1, freq="D")
    try:
        serie_obs = df_evi.loc[rango, col].values
    except KeyError:
        return np.nan
    ref_trunc = mu_ref[: t_actual + 1]
    if np.std(serie_obs) == 0:
        return 0.0
    r, _ = pearsonr(serie_obs, ref_trunc)
    return 0.0 if np.isnan(r) else r

def pendiente_verdeo_truncada(id_parcela, sos, t_actual, df_evi, dia_ini=5):
    col = f"id_{id_parcela}"
    dia_fin = min(t_actual - 5, t_actual)  # usa el punto más reciente disponible dentro de la ventana
    if dia_fin <= dia_ini:
        return np.nan
    rango = pd.date_range(start=sos, periods=t_actual + 1, freq="D")
    try:
        serie = df_evi.loc[rango, col].values
    except KeyError:
        return np.nan
    dias = np.arange(len(serie))
    evi_ini = np.interp(dia_ini, dias, serie)
    evi_fin = np.interp(dia_fin, dias, serie)
    return (evi_fin - evi_ini) / (dia_fin - dia_ini)

def extraer_matrices_por_ciclo(df_indice, tabla_filtrada, ventana=60):
    matrices, ids_usados = [], []
    for idx, ciclo in tabla_filtrada.iterrows():
        id_p = ciclo["id_parcela"]
        col = f"id_{id_p}"
        sos = ciclo["sos_fecha"]
        rango_fechas = pd.date_range(start=sos, periods=ventana + 1, freq="D")
        try:
            serie_alineada = df_indice.loc[rango_fechas, col].values
            matrices.append(serie_alineada)
            ids_usados.append(id_p)
        except KeyError:
            print(f"⚠️ Sin datos para '{col}' en el rango del SOS {sos.strftime('%Y-%m-%d')}")
            continue
    return np.array(matrices), ids_usados

def construir_patron(nombre_subtipo, tabla, df_evi, ventana=60):
    ref = tabla[tabla["subtipo_maiz"] == nombre_subtipo]
    matriz, ids = extraer_matrices_por_ciclo(df_evi, ref, ventana=ventana)
    return matriz, ids, np.mean(matriz, axis=0)

def rango_pendiente_por_dia(matriz_ids, tabla, df_evi, dias):
    out = {}
    for t in dias:
        vals = []
        for id_p in matriz_ids:
            sos = tabla[tabla["id_parcela"] == id_p]["sos_fecha"].iloc[0]
            vals.append(pendiente_verdeo_truncada(id_p, sos, t, df_evi))
        out[t] = (np.median(vals), vals)  # guardamos también los valores crudos para inspección
    return out

# Normalización por mediana con banda de tolerancia
def evaluar_score_v3(id_parcela, sos, t_actual, df_evi, mu_ref, mediana_pendiente_ref, tolerancia=0.5):
    r = correlacion_truncada(id_parcela, sos, t_actual, mu_ref, df_evi)
    pend_obs = pendiente_verdeo_truncada(id_parcela, sos, t_actual, df_evi)
    if np.isnan(r) or np.isnan(pend_obs) or mediana_pendiente_ref <= 0:
        return {"r_forma": np.nan, "pendiente_obs": np.nan, "score_compuesto": np.nan}
    # ratio contra la mediana, capado en 1.0; tolerancia define qué tan por debajo de la mediana aún es aceptable
    ratio = min(1.0, max(0.0, (pend_obs / mediana_pendiente_ref - tolerancia) / (1 - tolerancia)))
    score = max(0.0, r) * ratio * 100
    return {"r_forma": r, "pendiente_obs": pend_obs, "score_compuesto": score}

def cargar_mediana_pendiente_desde_bd(conn, subtipo, dia, version=None):
    query = """
        SELECT mediana_pendiente_verdeo FROM patron_referencia_fenologico
        WHERE subtipo = ? AND dia_post_sos = ?
        {}
    """.format("AND version = ?" if version else
               "AND version = (SELECT MAX(version) FROM patron_referencia_fenologico WHERE subtipo = ?)")
    params = (subtipo, dia, version) if version else (subtipo, dia, subtipo)
    row = pd.read_sql(query, conn, params=params)
    return None if row.empty else row["mediana_pendiente_verdeo"].iloc[0]


def clasificar_parcela_actual(conn, id_parcela, sos_fecha, df_evi, fecha_evaluacion=None):
    """Función principal de la plataforma: evalúa una parcela contra ambos patrones y devuelve el mejor score."""
    fecha_evaluacion = fecha_evaluacion or pd.Timestamp.today().normalize()
    t_actual = (fecha_evaluacion - sos_fecha).days

    if t_actual < 30:
        return {"estado": "fuera_de_ventana", "motivo": f"día {t_actual} < 30, aún no alcanza ventana mínima"}
    if t_actual > 60:
        t_actual = 60  # se evalúa con la ventana completa disponible, no se extrapola más allá

    resultados = {}
    for subtipo in ["grano_rapido", "grano_lento"]:
        mu_ref = cargar_patron_desde_bd(conn, subtipo)
        mediana_pend = cargar_mediana_pendiente_desde_bd(conn, subtipo, t_actual)
        if mu_ref is None or len(mu_ref) == 0 or mediana_pend is None:
            continue
        res = evaluar_score_v3(id_parcela, sos_fecha, t_actual, df_evi, mu_ref, mediana_pend)
        resultados[subtipo] = res

    if not resultados:
        return {"estado": "sin_patron_disponible"}

    mejor_subtipo = max(resultados, key=lambda k: resultados[k]["score_compuesto"] or 0)
    mejor = resultados[mejor_subtipo]

    return {
        "estado": "evaluado",
        "id_parcela": id_parcela,
        "dia_post_sos": t_actual,
        "patron_usado": mejor_subtipo,
        "r_forma": mejor["r_forma"],
        "pendiente_obs": mejor["pendiente_obs"],
        "score_compuesto": mejor["score_compuesto"],
    }