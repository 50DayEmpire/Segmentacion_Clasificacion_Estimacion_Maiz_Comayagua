# -*- coding: utf-8 -*-
"""
Diagnostico paso a paso: Notebook vs Pipeline.

Para un ciclo dado, reproduce exactamente los pasos del pipeline
(ejecutar_prediccion_ventana) y del notebook (prediccion_ventana_memoria)
para CADA ventana (T1, T2, T3, EOS) y compara cada variable intermedia.

Puntos de divergencia que este test busca identificar:

  1. RANGO DE SUAVIZADO WHITTAKER
     Pipeline: indices suavizados de BD (pre-suavizados fecha_inicio -> fecha_hoy)
     Notebook T1/T2/T3: re-suaviza fecha_inicio -> fecha_v (mas corto, Whittaker
     produce valores distintos en bordes)

  2. EOS TARGET PARA EXTRAPOLACION
     Pipeline: eos_ts = sos + DURACION_CICLO (160 dias)
     Notebook: eos_effective = eos real de BD (tipicamente 57-157 dias)
     -> Afecta: duracion_ciclo_dias en la curva, hasta donde se extrapola,
        rango de la serie climatica, y donde se recorta GPP.

  3. PARAMETRO valor_valle
     Pipeline: pasa valor_valle_evi/lswi almacenado en BD
     Notebook: NO lo pasa -> cotas vmin diferentes

  4. RANGO DE CARGA DE CLIMA
     Pipeline: fecha_inicio -> fecha_hoy
     Notebook: fecha_inicio -> eos (para EOS) o eos_effective (= eos, para T*)

  5. RANGO DE RECORTE GPP
     Pipeline: sos -> eos_ts (= sos + DURACION_CICLO para T*)
     Notebook: sos -> eos_effective (= eos real para todas las ventanas)
"""

import sys, warnings
sys.path.insert(0, '.')
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from datetime import date, timedelta
from contextlib import closing

from pipeline.ingesta import cargar_indices_desde_bd, cargar_clima_desde_bd
from pipeline.modulo_vpm import (
    preprocesar_indices_vpm,
    calcular_gpp_vpm,
    calcular_biomasa_y_rendimiento,
)
from pipeline.modulo_predictivo import (
    extender_serie_con_curva_parametrica,
    construir_serie_climatica_prediccion,
    obtener_climatologia,
)
from utils.conexionDB import set_db_path, get_connection_raw, get_db_path
from config import GPKG_PRUEBAS_PATH, DURACION_CICLO


# ── helpers ──────────────────────────────────────────────────────────────────

def cargar_indices_suavizados_ciclo(id_ciclo: int, id_parcela: int):
    """Carga indices suavizados ya persistidos en BD (indices_suavizados)."""
    sql = """
        SELECT fecha, evi, lswi
        FROM indices_suavizados
        WHERE id_ciclo = ? AND id_parcela = ?
        ORDER BY fecha
    """
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(id_ciclo, id_parcela), parse_dates=["fecha"])

    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    df["fecha"] = pd.to_datetime(df["fecha"]).dt.normalize()
    df = df.drop_duplicates(subset="fecha").set_index("fecha").sort_index()

    evi = df["evi"].dropna()
    lswi = df["lswi"].dropna() if "lswi" in df.columns else pd.Series(dtype=float)
    return evi, lswi


def comparar_series(s1, s2, nombre, tolerance=1e-8):
    """Compara dos series e imprime estadisticas de diferencia."""
    idx_comun = s1.index.intersection(s2.index)
    if len(idx_comun) == 0:
        print("  {}: SIN INDICES EN COMUN".format(nombre))
        return False

    v1 = s1.loc[idx_comun].values
    v2 = s2.loc[idx_comun].values

    diff = v1 - v2
    max_abs = np.abs(diff).max()
    mean_abs = np.abs(diff).mean()
    rmse = np.sqrt(np.mean(diff**2))
    iguales = np.allclose(v1, v2, atol=tolerance)

    status = "OK" if iguales else "DIF"
    print("  {}: n={} | max_diff={:.6f} | mean_diff={:.6f} | rmse={:.6f} | {}".format(
        nombre, len(idx_comun), max_abs, mean_abs, rmse, status))

    if not iguales:
        idx_diff = np.where(np.abs(diff) > tolerance)[0]
        print("    Primeras 3 diferencias:")
        for i in idx_diff[:3]:
            print("      {}: pipe={:.6f}  nb={:.6f}  diff={:.6f}".format(
                idx_comun[i].date(), v2[i], v1[i], diff[i]))
    return iguales


def cargar_ciclo_info(id_ciclo):
    """Carga metadatos de produccion_acumulada_ciclo."""
    sql = """
        SELECT sos, eos, fecha_inicio, lswi_max, rendimiento, temporada,
               lswi_max_efectivo
        FROM produccion_acumulada_ciclo
        WHERE id_ciclo = ?
    """
    with closing(get_connection_raw()) as conn:
        row = conn.execute(sql, (id_ciclo,)).fetchone()

    if not row:
        return None

    return {
        "sos": pd.Timestamp(row[0]),
        "eos": pd.Timestamp(row[1]),
        "fecha_inicio": pd.Timestamp(row[2]) if row[2] else pd.Timestamp(row[0]),
        "lswi_max_db": float(row[3]) if row[3] else None,
        "rendimiento": float(row[4]) if row[4] else None,
        "temporada": row[5],
        "lswi_max_efectivo": float(row[6]) if row[6] else None,
    }


def diagnosticar_ventana(id_ciclo, id_parcela, info, ventana, fecha_hoy=None):
    """
    Diagnostico para UNA ventana (T1/T2/T3/EOS).
    Reproduce pipeline y notebook, comparando paso a paso.
    """
    if fecha_hoy is None:
        fecha_hoy = date.today()

    sos = info["sos"]
    eos = info["eos"]
    fecha_inicio = info["fecha_inicio"]
    lswi_max_db = info["lswi_max_db"]
    col = "id_{}".format(id_parcela)

    VENTANA_DIAS = {"T1": 30, "T2": 60, "T3": 90, "EOS": None}
    if ventana not in VENTANA_DIAS:
        return

    print("\n" + "=" * 80)
    print("VENTANA {} | ciclo={} parcela={} | sos={} eos={} inicio={}".format(
        ventana, id_ciclo, id_parcela, sos.date(), eos.date(), fecha_inicio.date()))
    print("=" * 80)

    dias_desde_sos = VENTANA_DIAS[ventana]
    if ventana == "EOS":
        fecha_ventana = eos
        eos_ts_pipe = eos
        eos_ts_nb = eos
    else:
        fecha_ventana = sos + timedelta(days=dias_desde_sos)
        eos_ts_pipe = sos + timedelta(days=DURACION_CICLO)  # Pipeline: SOS + 160
        eos_ts_nb = eos  # Notebook: EOS real

    print("  fecha_ventana={} | eos_ts_pipe={} | eos_ts_nb={}".format(
        fecha_ventana.date(), eos_ts_pipe.date(), eos_ts_nb.date()))
    print("  Diferencia eos_ts: {} dias".format((eos_ts_pipe - eos_ts_nb).days))

    # ── CLIMATOLOGIA (compartida) ──────────────────────────────────────────
    clim_rad = obtener_climatologia("radiacion")
    clim_temp = obtener_climatologia("temperatura")

    difference_found = False

    # ═══════════════════════════════════════════════════════════════════════
    # PASO A: INDICES BASE
    # ═══════════════════════════════════════════════════════════════════════
    print("\n--- A: INDICES BASE ---")

    # A1: Pipeline - indices suavizados de BD
    evi_pipe_full, lswi_pipe_full = cargar_indices_suavizados_ciclo(id_ciclo, id_parcela)
    print("  Pipeline: evi={} obs, lswi={} obs, rango={} a {}".format(
        len(evi_pipe_full), len(lswi_pipe_full),
        evi_pipe_full.index.min().date() if not evi_pipe_full.empty else "N/A",
        evi_pipe_full.index.max().date() if not evi_pipe_full.empty else "N/A"))

    # Pipeline usa toda la serie para W_scalar y FPAR
    evi_pipe_ventana = evi_pipe_full.loc[sos:eos_ts_pipe].dropna()
    lswi_pipe_ventana = lswi_pipe_full.loc[sos:eos_ts_pipe].dropna()

    # A2: Notebook - depende de la ventana
    if ventana == "EOS":
        # Notebook EOS: indices suavizados de BD (misma fuente)
        evi_nb_ventana = evi_pipe_full.loc[sos:eos].dropna()
        lswi_nb_ventana = lswi_pipe_full.loc[sos:eos].dropna()
        print("  Notebook EOS: indices_suavizados (misma fuente)")
    else:
        # Notebook T1/T2/T3: re-suavizado fecha_inicio -> fecha_ventana
        print("  Notebook {}: re-suavizando fecha_inicio={} -> fecha_ventana={}".format(
            ventana, fecha_inicio.date(), fecha_ventana.date()))
        dfs_raw = cargar_indices_desde_bd(
            fecha_inicio=str(fecha_inicio.date()),
            fecha_fin=str(fecha_ventana.date()),
            ids_parcelas=[id_parcela],
        )
        dfs_vpm = preprocesar_indices_vpm(dfs_raw)
        if col in dfs_vpm["EVI"].columns:
            evi_nb_ventana = dfs_vpm["EVI"][col].loc[sos:eos_ts_nb].dropna()
            lswi_nb_ventana = dfs_vpm["LSWI"][col].loc[sos:eos_ts_nb].dropna()
        else:
            evi_nb_ventana = pd.Series(dtype=float)
            lswi_nb_ventana = pd.Series(dtype=float)
        print("  Notebook: evi={} obs, lswi={} obs, rango={} a {}".format(
            len(evi_nb_ventana), len(lswi_nb_ventana),
            evi_nb_ventana.index.min().date() if not evi_nb_ventana.empty else "N/A",
            evi_nb_ventana.index.max().date() if not evi_nb_ventana.empty else "N/A"))

    # A3: Comparar indices base en rango comun (sos:eos)
    rango_comun = sos if sos > min(evi_pipe_ventana.index.min(), evi_nb_ventana.index.min()) else max(evi_pipe_ventana.index.min(), evi_nb_ventana.index.min())
    if not evi_pipe_ventana.empty and not evi_nb_ventana.empty:
        comunes = evi_pipe_ventana.index.intersection(evi_nb_ventana.index)
        if len(comunes) > 0:
            diff_evi = evi_pipe_ventana.loc[comunes] - evi_nb_ventana.loc[comunes]
            max_d = np.abs(diff_evi).max()
            if max_d > 1e-8:
                difference_found = True
            print("  EVI comunes={} | max_diff={:.6f}".format(len(comunes), max_d))
        else:
            print("  EVI: SIN fechas comunes!")
    else:
        print("  EVI: alguna serie vacia")

    # ═══════════════════════════════════════════════════════════════════════
    # PASO B: EXTRAPOLACION (solo T1/T2/T3, EOS usa observado)
    # ═══════════════════════════════════════════════════════════════════════
    print("\n--- B: EXTRAPOLACION ---")

    if ventana == "EOS":
        # Para EOS, ambos usan observado directamente
        evi_pipe_series = evi_pipe_ventana
        lswi_pipe_series = lswi_pipe_ventana
        evi_nb_series = evi_nb_ventana
        lswi_nb_series = lswi_nb_ventana
        params_pipe_evi = None
        params_pipe_lswi = None
        params_nb_evi = None
        params_nb_lswi = None
        print("  EOS: sin extrapolacion (ambos usan observado)")
    else:
        # Pipeline: pasa valor_valle=None (columna no existe en BD),
        #            eos_ts = sos + DURACION_CICLO
        print("  Pipeline: sin valor_valle (no disponible), eos_ts={}".format(eos_ts_pipe.date()))

        evi_pipe_series, params_pipe_evi = extender_serie_con_curva_parametrica(
            evi_pipe_ventana, sos, eos_ts_pipe, ventana=ventana,
            valor_valle=None,
        )
        lswi_pipe_series, params_pipe_lswi = extender_serie_con_curva_parametrica(
            lswi_pipe_ventana, sos, eos_ts_pipe, ventana=ventana,
            valor_valle=None,
        )
        if params_pipe_evi:
            print("  Pipeline EVI params: vmin={:.4f} vmax={:.4f} r2={:.4f}".format(
                params_pipe_evi["vmin"], params_pipe_evi["vmax"], params_pipe_evi["r2"]))
        else:
            print("  Pipeline EVI params: SIN AJUSTE")

        # Notebook: NO pasa valor_valle, eos_ts = eos real
        print("  Notebook: sin valor_valle, eos_ts={}".format(eos_ts_nb.date()))

        evi_nb_series, params_nb_evi = extender_serie_con_curva_parametrica(
            evi_nb_ventana, sos, eos_ts_nb, ventana=ventana,
            valor_valle=None,
        )
        lswi_nb_series, params_nb_lswi = extender_serie_con_curva_parametrica(
            lswi_nb_ventana, sos, eos_ts_nb, ventana=ventana,
            valor_valle=None,
        )
        if params_nb_evi:
            print("  Notebook EVI params: vmin={:.4f} vmax={:.4f} r2={:.4f}".format(
                params_nb_evi["vmin"], params_nb_evi["vmax"], params_nb_evi["r2"]))
        else:
            print("  Notebook EVI params: SIN AJUSTE")

        # Comparar parametros
        if params_pipe_evi and params_nb_evi:
            for k in ["vmin", "vmax", "S", "mS", "A", "mA", "r2"]:
                vp = params_pipe_evi.get(k, "N/A")
                vn = params_nb_evi.get(k, "N/A")
                if vp != "N/A" and vn != "N/A":
                    d = abs(vp - vn)
                    if d > 1e-4:
                        difference_found = True
                        print("    DIF params EVI: {} pipe={} nb={} diff={:.6f}".format(k, vp, vn, d))

    # B3: Comparar series extendidas en rango comun
    comunes = evi_pipe_series.index.intersection(evi_nb_series.index)
    if len(comunes) > 0:
        diff_evi = evi_pipe_series.loc[comunes] - evi_nb_series.loc[comunes]
        max_d = np.abs(diff_evi).max()
        mean_d = np.abs(diff_evi).mean()
        if max_d > 1e-8:
            difference_found = True
        print("  EVI extendido comunes={} | max_diff={:.6f} mean_diff={:.6f}".format(
            len(comunes), max_d, mean_d))
    else:
        print("  EVI extendido: SIN fechas comunes!")

    # ═══════════════════════════════════════════════════════════════════════
    # PASO C: FPAR Y W_SCALAR
    # ═══════════════════════════════════════════════════════════════════════
    print("\n--- C: FPAR y W_scalar ---")

    def calc_lswi_max(serie_lswi, lswi_max_db):
        lswi_max_serie = float(serie_lswi.max()) if not serie_lswi.empty else None
        if lswi_max_db is not None and lswi_max_serie is not None:
            return max(lswi_max_db, lswi_max_serie)
        return lswi_max_db if lswi_max_db is not None else lswi_max_serie

    # Pipeline
    lswi_max_pipe = calc_lswi_max(lswi_pipe_series, lswi_max_db)
    df_evi_p = pd.DataFrame({col: evi_pipe_series})
    df_lswi_p = pd.DataFrame({col: lswi_pipe_series})
    df_w_p = (1.0 + df_lswi_p) / (1.0 + lswi_max_pipe) if lswi_max_pipe else df_lswi_p.copy()
    df_fpar_p = 1.0 * df_evi_p
    print("  Pipeline: lswi_max={:.4f} | W_scalar mean={:.4f} max={:.4f}".format(
        lswi_max_pipe, df_w_p[col].mean(), df_w_p[col].max()))

    # Notebook
    lswi_max_nb = calc_lswi_max(lswi_nb_series, lswi_max_db)
    df_evi_n = pd.DataFrame({col: evi_nb_series})
    df_lswi_n = pd.DataFrame({col: lswi_nb_series})
    df_w_n = (1.0 + df_lswi_n) / (1.0 + lswi_max_nb) if lswi_max_nb else df_lswi_n.copy()
    df_fpar_n = 1.0 * df_evi_n
    print("  Notebook: lswi_max={:.4f} | W_scalar mean={:.4f} max={:.4f}".format(
        lswi_max_nb, df_w_n[col].mean(), df_w_n[col].max()))

    if abs(lswi_max_pipe - lswi_max_nb) > 1e-8:
        difference_found = True
        print("  DIF lswi_max: pipe={} nb={}".format(lswi_max_pipe, lswi_max_nb))

    # ═══════════════════════════════════════════════════════════════════════
    # PASO D: CLIMA
    # ═══════════════════════════════════════════════════════════════════════
    print("\n--- D: CLIMA ---")

    # Pipeline: carga clima fecha_inicio -> fecha_hoy
    dfs_clima_raw_p = cargar_clima_desde_bd(
        fecha_inicio=str(fecha_inicio.date()),
        fecha_fin=str(fecha_hoy),
        ids_parcelas=[id_parcela],
    )
    temp_real_p = dfs_clima_raw_p["temperature-mean"][col].dropna()
    rad_real_p = dfs_clima_raw_p["solar-radiation-flux"][col].dropna()
    print("  Pipeline clima: temp={} obs, rad={} obs, rango={} a {}".format(
        len(temp_real_p), len(rad_real_p),
        temp_real_p.index.min().date(), temp_real_p.index.max().date()))

    # Notebook: carga clima fecha_inicio -> eos_ts_nb
    dfs_clima_raw_n = cargar_clima_desde_bd(
        fecha_inicio=str(fecha_inicio.date()),
        fecha_fin=str(eos_ts_nb.date()),
        ids_parcelas=[id_parcela],
    )
    temp_real_n = dfs_clima_raw_n["temperature-mean"][col].dropna()
    rad_real_n = dfs_clima_raw_n["solar-radiation-flux"][col].dropna()
    print("  Notebook clima: temp={} obs, rad={} obs, rango={} a {}".format(
        len(temp_real_n), len(rad_real_n),
        temp_real_n.index.min().date(), temp_real_n.index.max().date()))

    # Construir series climaticas completas
    fechas_p = evi_pipe_series.index
    fechas_n = evi_nb_series.index
    print("  Fechas vegetacion pipe: {} ({} a {})".format(
        len(fechas_p), fechas_p.min().date(), fechas_p.max().date()))
    print("  Fechas vegetacion nb:   {} ({} a {})".format(
        len(fechas_n), fechas_n.min().date(), fechas_n.max().date()))

    serie_temp_p = construir_serie_climatica_prediccion(
        fecha_inicio, eos_ts_pipe, temp_real_p, clim_temp,
    ).reindex(fechas_p)
    serie_rad_p = construir_serie_climatica_prediccion(
        fecha_inicio, eos_ts_pipe, rad_real_p, clim_rad,
    ).reindex(fechas_p)

    serie_temp_n = construir_serie_climatica_prediccion(
        fecha_inicio, eos_ts_nb, temp_real_n, clim_temp,
    ).reindex(fechas_n)
    serie_rad_n = construir_serie_climatica_prediccion(
        fecha_inicio, eos_ts_nb, rad_real_n, clim_rad,
    ).reindex(fechas_n)

    # Comparar clima en fechas comunes
    comunes_temp = serie_temp_p.index.intersection(serie_temp_n.index)
    if len(comunes_temp) > 0:
        d = np.abs(serie_temp_p.loc[comunes_temp] - serie_temp_n.loc[comunes_temp]).max()
        print("  Temp comunes={} max_diff={:.6f}".format(len(comunes_temp), d))
        if d > 1e-8:
            difference_found = True

    comunes_rad = serie_rad_p.index.intersection(serie_rad_n.index)
    if len(comunes_rad) > 0:
        d = np.abs(serie_rad_p.loc[comunes_rad] - serie_rad_n.loc[comunes_rad]).max()
        print("  Rad comunes={} max_diff={:.6f}".format(len(comunes_rad), d))
        if d > 1e-8:
            difference_found = True

    # ═══════════════════════════════════════════════════════════════════════
    # PASO E: GPP Y YIELD
    # ═══════════════════════════════════════════════════════════════════════
    print("\n--- E: GPP y YIELD ---")

    dfs_veg_p = {"EVI": df_evi_p, "LSWI": df_lswi_p, "FPAR": df_fpar_p, "W_scalar": df_w_p}
    dfs_clima_p = {"temperature-mean": pd.DataFrame({col: serie_temp_p}),
                   "solar-radiation-flux": pd.DataFrame({col: serie_rad_p})}
    dfs_veg_n = {"EVI": df_evi_n, "LSWI": df_lswi_n, "FPAR": df_fpar_n, "W_scalar": df_w_n}
    dfs_clima_n = {"temperature-mean": pd.DataFrame({col: serie_temp_n}),
                   "solar-radiation-flux": pd.DataFrame({col: serie_rad_n})}

    try:
        dfs_gpp_p = calcular_gpp_vpm(dfs_veg_p, dfs_clima_p)
    except Exception as e:
        print("  Pipeline GPP ERROR: {}".format(e))
        return

    try:
        dfs_gpp_n = calcular_gpp_vpm(dfs_veg_n, dfs_clima_n)
    except Exception as e:
        print("  Notebook GPP ERROR: {}".format(e))
        return

    # Recorte GPP
    gpp_p_full = dfs_gpp_p["GPP"][col]
    gpp_n_full = dfs_gpp_n["GPP"][col]

    # Pipeline recorta a sos:eos_ts_pipe
    gpp_p_rec = gpp_p_full.loc[sos:eos_ts_pipe].dropna()
    # Notebook recorta a sos:eos_ts_nb
    gpp_n_rec = gpp_n_full.loc[sos:eos_ts_nb].dropna()

    print("  GPP pipe: full={} dias, recortado={} dias, sum={:.2f}".format(
        len(gpp_p_full), len(gpp_p_rec), gpp_p_rec.sum()))
    print("  GPP nb:   full={} dias, recortado={} dias, sum={:.2f}".format(
        len(gpp_n_full), len(gpp_n_rec), gpp_n_rec.sum()))

    # Comparar GPP en rango comun (sos:eos real, que es lo que usa el notebook)
    comunes_gpp = gpp_p_full.index.intersection(gpp_n_full.index)
    if len(comunes_gpp) > 0:
        gpp_c = gpp_p_full.loc[comunes_gpp] - gpp_n_full.loc[comunes_gpp]
        print("  GPP comunes={} max_diff={:.6f} mean_diff={:.6f} rmse={:.6f}".format(
            len(comunes_gpp), np.abs(gpp_c).max(), np.abs(gpp_c).mean(),
            np.sqrt(np.mean(gpp_c**2))))
        if np.abs(gpp_c).max() > 1e-8:
            difference_found = True

    # Yield
    res_p = calcular_biomasa_y_rendimiento(gpp_p_rec)
    res_n = calcular_biomasa_y_rendimiento(gpp_n_rec)
    yield_p = float(res_p["yield_final_tha"].iloc[0]) * 22.0458
    yield_n = float(res_n["yield_final_tha"].iloc[0]) * 22.0458
    rend = info["rendimiento"] if info["rendimiento"] else 0

    # Para comparacion justa, calcular yield de pipeline con recorte a eos real
    if ventana != "EOS":
        gpp_p_rec_eos = gpp_p_full.loc[sos:eos].dropna()
        if not gpp_p_rec_eos.empty:
            res_p_eos = calcular_biomasa_y_rendimiento(gpp_p_rec_eos)
            yield_p_eos = float(res_p_eos["yield_final_tha"].iloc[0]) * 22.0458
        else:
            yield_p_eos = None
        print("  Yield pipe (recorte a eos pipe={}): {:.2f} qq/ha".format(
            eos_ts_pipe.date(), yield_p))
        print("  Yield pipe (recorte a eos real={}):  {:.2f} qq/ha".format(
            eos.date(), yield_p_eos if yield_p_eos else 0))
    else:
        print("  Yield pipe: {:.2f} qq/ha".format(yield_p))

    print("  Yield nb:   {:.2f} qq/ha".format(yield_n))
    print("  Yield real: {:.2f} qq/ha".format(rend))

    # ═══════════════════════════════════════════════════════════════════════
    # RESUMEN
    # ═══════════════════════════════════════════════════════════════════════
    if ventana != "EOS":
        err_p = yield_p - rend
        err_n = yield_n - rend
        extra_p = (eos_ts_pipe - eos).days
        print("\n  RESULTADOS:")
        print("  Yield pipe  = {:.2f} (error={:.2f}, extra_dias_gpp={})".format(yield_p, err_p, extra_p))
        print("  Yield nb    = {:.2f} (error={:.2f})".format(yield_n, err_n))
        if yield_p_eos:
            err_p_eos = yield_p_eos - rend
            print("  Yield pipe* = {:.2f} (error={:.2f}, recorte a eos real)".format(yield_p_eos, err_p_eos))
    else:
        err_p = yield_p - rend
        err_n = yield_n - rend
        print("  Yield pipe  = {:.2f} (error={:.2f})".format(yield_p, err_p))
        print("  Yield nb    = {:.2f} (error={:.2f})".format(yield_n, err_n))

    if not difference_found:
        print("\n  >>> IDENTICOS <<<")
    else:
        print("\n  >>> HAY DIFERENCIAS <<<")

    return {
        "ventana": ventana,
        "difference_found": difference_found,
        "yield_pipe": yield_p,
        "yield_nb": yield_n,
        "yield_real": rend,
    }


if __name__ == "__main__":
    set_db_path(GPKG_PRUEBAS_PATH)
    print("BD: {}".format(get_db_path()))

    ciclos_test = [2615, 2618, 2620, 2622, 2623, 2625, 2626, 2627, 2628]
    ventanas = ["T1", "T2", "T3", "EOS"]

    for id_ciclo in ciclos_test:
        with closing(get_connection_raw()) as conn:
            row = conn.execute(
                "SELECT id_parcela FROM produccion_acumulada_ciclo WHERE id_ciclo=?",
                (id_ciclo,)
            ).fetchone()

        if not row:
            print("\nCiclo {} no encontrado".format(id_ciclo))
            continue

        id_parcela = row[0]
        info = cargar_ciclo_info(id_ciclo)
        if info is None:
            continue

        for v in ventanas:
            try:
                diagnosticar_ventana(id_ciclo, id_parcela, info, v)
            except Exception as e:
                import traceback
                print("\nERROR en ciclo {} ventana {}: {}".format(id_ciclo, v, e))
                traceback.print_exc()
