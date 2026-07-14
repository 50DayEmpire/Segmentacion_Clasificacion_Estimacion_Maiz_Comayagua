from __future__ import annotations

import logging
from contextlib import closing
from datetime import date

from scipy.stats import pearsonr
import pandas as pd
import numpy as np
from tqdm import tqdm

from utils.conexionDB import get_connection_raw

_log_clf = logging.getLogger(__name__)

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

def _score_a_label(score):
    """Convierte score_compuesto a etiqueta de clasificación."""
    if pd.isna(score):
        return "Incierto"
    if score >= 70:
        return "Maíz"
    if score >= 30:
        return "Maíz - baja probabilidad"
    return "Otro"

def persistir_clasificacion_v2(conn, resultado, id_ciclo, ventana=None):
    """
    Persiste los scores de clasificación en ``predicciones_ventana``
    y, si el resultado está evaluado, actualiza ``clasificacion_final``
    en ``produccion_acumulada_ciclo``.

    Parámetros
    ----------
    conn : sqlite3.Connection
        Conexión a la BD.
    resultado : dict
        Salida de ``clasificar_parcela_actual()``.
    id_ciclo : int
        Identificador del ciclo.
    ventana : str | None
        Ventana de predicción asociada (T1/T2/T3/EOS).
        Si es ``None`` se infiere de ``dia_post_sos``.
    """
    if resultado["estado"] != "evaluado":
        _log_clf.debug("Ciclo %s: estado '%s', no se persiste", id_ciclo, resultado["estado"])
        return

    score = resultado.get("score_compuesto")
    label = _score_a_label(score)
    dia = resultado.get("dia_post_sos")

    if ventana is None:
        ventana = "T1" if dia <= 30 else ("T2" if dia <= 60 else "T3")

    with conn:
        cur = conn.execute(
            """UPDATE predicciones_ventana
               SET score_pearson = ?,
                   score_magnitud_pendiente = ?,
                   score_compuesto = ?,
                   cultivo_predicho = ?
               WHERE id_ciclo = ? AND ventana = ?""",
            (
                resultado.get("r_forma"),
                resultado.get("pendiente_obs"),
                score,
                label,
                id_ciclo,
                ventana,
            ),
        )

        if cur.rowcount == 0:
            conn.execute(
                """INSERT INTO predicciones_ventana
                   (id_ciclo, id_parcela, ventana, fecha_ventana,
                    score_pearson, score_magnitud_pendiente,
                    score_compuesto, cultivo_predicho, fecha_congelamiento)
                   VALUES (?, ?, ?, DATE('now'),
                           ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    id_ciclo,
                    resultado.get("id_parcela"),
                    ventana,
                    resultado.get("r_forma"),
                    resultado.get("pendiente_obs"),
                    score,
                    label,
                ),
            )

        conn.execute(
            "UPDATE produccion_acumulada_ciclo SET clasificacion_final = ? WHERE id_ciclo = ?",
            (label, id_ciclo),
        )

    _log_clf.info(
        "[CLF] Ciclo %s → %s (score=%.1f%%, r=%.3f, pend=%.4f, ventana=%s)",
        id_ciclo, label, score if pd.notna(score) else 0,
        resultado.get("r_forma") or 0,
        resultado.get("pendiente_obs") or 0,
        ventana,
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


def seed_clasificacion(
    conn,
    temporada: str | None = None,
    ids_parcelas: list[int] | None = None,
    fecha_hoy: date | None = None,
    logger: logging.Logger | None = None,
) -> dict:
    """
    Itera sobre ciclos sin ``clasificacion_final``, carga EVI desde BD,
    ejecuta ``clasificar_parcela_actual()`` y persiste los scores.

    Parámetros
    ----------
    conn : sqlite3.Connection
    temporada : str | None
        Filtrar por temporada (``"primera"`` / ``"postrera"``).
        ``None`` = todas las temporadas.
    ids_parcelas : list[int] | None
        Filtrar por parcelas específicas.  ``None`` = todas.
    fecha_hoy : date | None
        Fecha de evaluación.  ``None`` = ``date.today()``.
    logger : logging.Logger | None
        Logger externo (ej. el del worker).  ``None`` = usa el interno.

    Retorna
    -------
    dict
        ``{"total": int, "clasificados": int, "fuera_ventana": int,
          "sin_patron": int, "sin_evi": int, "errores": list[str]}``
    """
    from pipeline.ingesta import cargar_indices_desde_bd
    from pipeline.modulo_vpm import preprocesar_indices_vpm

    log = logger or _log_clf
    fecha_hoy = fecha_hoy or date.today()

    params: list = []
    where_clauses: list[str] = ["pac.clasificacion_final IS NULL",
                                 "pac.sos IS NOT NULL"]
    if temporada:
        where_clauses.append("pac.temporada = ?")
        params.append(temporada)
    if ids_parcelas:
        placeholders = ",".join("?" for _ in ids_parcelas)
        where_clauses.append(f"pac.id_parcela IN ({placeholders})")
        params.extend(ids_parcelas)

    sql = f"""
        WITH ultima_ventana AS (
            SELECT id_ciclo, ventana, fecha_ventana,
                   ROW_NUMBER() OVER (
                       PARTITION BY id_ciclo
                       ORDER BY
                           CASE WHEN score_compuesto IS NOT NULL THEN 0 ELSE 1 END,
                           CASE ventana
                               WHEN 'T1'  THEN 1
                               WHEN 'T2'  THEN 2
                               WHEN 'T3'  THEN 3
                               WHEN 'EOS' THEN 4
                           END DESC
                   ) AS rn
            FROM predicciones_ventana
        )
        SELECT pac.id_ciclo, pac.id_parcela, pac.sos, pac.temporada,
               uv.ventana AS ultima_ventana
        FROM produccion_acumulada_ciclo pac
        JOIN ultima_ventana uv ON uv.id_ciclo = pac.id_ciclo AND uv.rn = 1
        WHERE {' AND '.join(where_clauses)}
        ORDER BY pac.id_ciclo
    """
    ciclos = conn.execute(sql, params).fetchall()

    if not ciclos:
        log.info("[CLF] No se encontraron ciclos pendientes de clasificación.")
        return {"total": 0, "clasificados": 0, "fuera_ventana": 0,
                "sin_patron": 0, "sin_evi": 0, "errores": []}

    log.info("[CLF] %s ciclo(s) pendiente(s) de clasificación.", len(ciclos))

    clasificados = 0
    fuera_ventana = 0
    sin_patron = 0
    sin_evi = 0
    errores: list[str] = []
    _cache_proc: dict[int, pd.DataFrame] = {}

    for row in tqdm(ciclos, desc="Clasificando ciclos"):
        id_ciclo, id_parcela, sos_str, *_ = row

        if id_parcela not in _cache_proc:
            try:
                dfs = cargar_indices_desde_bd(ids_parcelas=[id_parcela])
            except ValueError:
                log.debug("[CLF] Ciclo %s: sin EVI en BD (ValueError)", id_ciclo)
                sin_evi += 1
                continue
            except Exception as exc:
                errores.append(f"Ciclo {id_ciclo}: error cargando índices – {exc}")
                log.warning("[CLF] Ciclo %s: error cargando índices – %s", id_ciclo, exc)
                continue

            if dfs.get("EVI") is None or dfs["EVI"].empty:
                log.debug("[CLF] Ciclo %s: DataFrame EVI vacío", id_ciclo)
                sin_evi += 1
                continue

            try:
                dfs_proc = preprocesar_indices_vpm(dfs)
            except Exception as exc:
                errores.append(f"Ciclo {id_ciclo}: error preprocesando índices – {exc}")
                log.warning("[CLF] Ciclo %s: error preprocesando índices – %s", id_ciclo, exc)
                continue

            _cache_proc[id_parcela] = dfs_proc["EVI"]

        df_evi = _cache_proc[id_parcela]
        sos_fecha = pd.Timestamp(sos_str)
        try:
            res = clasificar_parcela_actual(
                conn, id_parcela, sos_fecha, df_evi,
                fecha_evaluacion=pd.Timestamp(fecha_hoy),
            )
        except Exception as exc:
            errores.append(f"Ciclo {id_ciclo}: error clasificando – {exc}")
            log.warning("[CLF] Ciclo %s: error clasificando – %s", id_ciclo, exc)
            continue

        if res["estado"] == "evaluado":
            try:
                persistir_clasificacion_v2(conn, res, id_ciclo)
                clasificados += 1
            except Exception as exc:
                errores.append(
                    f"Ciclo {id_ciclo}: error persistiendo – {exc}"
                )
                log.warning("[CLF] Ciclo %s: error persistiendo – %s", id_ciclo, exc)
        elif res["estado"] == "fuera_de_ventana":
            fuera_ventana += 1
            log.debug("[CLF] Ciclo %s: %s", id_ciclo, res.get("motivo", "fuera de ventana"))
        elif res["estado"] == "sin_patron_disponible":
            sin_patron += 1
            log.warning("[CLF] Ciclo %s: sin patrón de referencia disponible", id_ciclo)
        else:
            sin_evi += 1
            log.debug("[CLF] Ciclo %s: estado '%s' – sin datos", id_ciclo, res["estado"])

    log.info(
        "[CLF] Seed completado: %d clasificados, %d fuera de ventana, "
        "%d sin patrón, %d sin EVI, %d errores",
        clasificados, fuera_ventana, sin_patron, sin_evi, len(errores),
    )

    return {
        "total": len(ciclos),
        "clasificados": clasificados,
        "fuera_ventana": fuera_ventana,
        "sin_patron": sin_patron,
        "sin_evi": sin_evi,
        "errores": errores,
    }


def clasificar_parcela_actual(conn, id_parcela, sos_fecha, df_evi, fecha_evaluacion=None):
    """Función principal de la plataforma: evalúa una parcela contra ambos patrones y devuelve el mejor score."""
    fecha_evaluacion = fecha_evaluacion or pd.Timestamp.today().normalize()
    t_actual = (fecha_evaluacion - sos_fecha).days

    if t_actual < 30:
        _log_clf.debug("[CLF] Parcela %s: %d días post-SOS, no alcanza mínimo 30",
                       id_parcela, t_actual)
        return {"estado": "fuera_de_ventana", "motivo": f"día {t_actual} < 30, aún no alcanza ventana mínima"}
    if t_actual > 60:
        _log_clf.debug("[CLF] Parcela %s: t_actual=%d, capado a 60", id_parcela, t_actual)
        t_actual = 60

    resultados = {}
    for subtipo in ["grano_rapido", "grano_lento"]:
        mu_ref = cargar_patron_desde_bd(conn, subtipo)
        mediana_pend = cargar_mediana_pendiente_desde_bd(conn, subtipo, t_actual)
        if mu_ref is None or len(mu_ref) == 0 or mediana_pend is None:
            _log_clf.debug("[CLF] Parcela %s: sin datos para patrón '%s' (t=%d)",
                           id_parcela, subtipo, t_actual)
            continue
        res = evaluar_score_v3(id_parcela, sos_fecha, t_actual, df_evi, mu_ref, mediana_pend)
        resultados[subtipo] = res
        _log_clf.debug("[CLF] Parcela %s, patrón '%s': r=%.3f, pend=%.4f, score=%.1f",
                       id_parcela, subtipo,
                       res.get("r_forma") or 0,
                       res.get("pendiente_obs") or 0,
                       res.get("score_compuesto") or 0)

    if not resultados:
        _log_clf.warning("[CLF] Parcela %s: ningún patrón disponible para clasificar", id_parcela)
        return {"estado": "sin_patron_disponible"}

    mejor_subtipo = max(resultados, key=lambda k: resultados[k]["score_compuesto"] or 0)
    mejor = resultados[mejor_subtipo]

    _log_clf.info("[CLF] Parcela %s: clasificado con '%s' (score=%.1f%%)",
                  id_parcela, mejor_subtipo, mejor.get("score_compuesto") or 0)

    return {
        "estado": "evaluado",
        "id_parcela": id_parcela,
        "dia_post_sos": t_actual,
        "patron_usado": mejor_subtipo,
        "r_forma": mejor["r_forma"],
        "pendiente_obs": mejor["pendiente_obs"],
        "score_compuesto": mejor["score_compuesto"],
    }