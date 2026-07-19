# -*- coding: utf-8 -*-
"""
Test de diagnostico: compara paso a paso el notebook vs pipeline
para encontrar donde divergen los resultados.
"""
import sys
import warnings
sys.path.insert(0, '.')
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from datetime import timedelta
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


def comparar_series(s1, s2, nombre, tolerance=1e-6):
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

    status = "OK" if iguales else "DIFERENTES"
    print("  {}: n={} | max_diff={:.6f} | mean_diff={:.6f} | rmse={:.6f} | {}".format(nombre, len(idx_comun), max_abs, mean_abs, rmse, status))

    if not iguales:
        idx_diff = np.where(np.abs(diff) > tolerance)[0]
        print("    Primeras diferencias:")
        for i in idx_diff[:5]:
            print("      {}: pipeline={:.6f} vs notebook={:.6f} (diff={:.6f})".format(
                idx_comun[i].date(), v2[i], v1[i], diff[i]))
    return iguales


def test_diagnostico_ciclo(id_ciclo: int, id_parcela: int):
    """Ejecuta diagnostico completo para un ciclo."""
    print("\n" + "="*80)
    print("DIAGNOSTICO CICLO {} - PARCELA {}".format(id_ciclo, id_parcela))
    print("="*80)

    sql = """
        SELECT sos, eos, fecha_inicio, lswi_max, rendimiento, temporada
        FROM produccion_acumulada_ciclo
        WHERE id_ciclo = ?
    """
    with closing(get_connection_raw()) as conn:
        row = conn.execute(sql, (id_ciclo,)).fetchone()

    if not row:
        print("Ciclo no encontrado")
        return

    sos = pd.Timestamp(row[0])
    eos = pd.Timestamp(row[1])
    fecha_inicio = pd.Timestamp(row[2]) if row[2] else sos
    lswi_max_db = float(row[3]) if row[3] else None
    rendimiento_final = float(row[4]) if row[4] else None
    temporada = row[5]

    print("Info ciclo: sos={} eos={} inicio={} lswi_max={} rendimiento_final={} temporada={}".format(
        sos.date(), eos.date(), fecha_inicio.date(), lswi_max_db, rendimiento_final, temporada))

    col = "id_{}".format(id_parcela)

    # 1. INDICES CRUDOS
    print("\n1. INDICES CRUDOS (series_diarias_vpm):")
    sql_raw = """
        SELECT fecha, evi_crudo, lswi_crudo
        FROM series_diarias_vpm
        WHERE id_parcela = ? AND fecha BETWEEN ? AND ?
        ORDER BY fecha
    """
    with closing(get_connection_raw()) as conn:
        df_raw = pd.read_sql(sql_raw, conn, params=(id_parcela, str(fecha_inicio.date()), str(eos.date())), parse_dates=["fecha"])

    if df_raw.empty:
        print("  SIN DATOS CRUDOS")
        return

    df_raw["fecha"] = pd.to_datetime(df_raw["fecha"]).dt.normalize()
    df_raw = df_raw.drop_duplicates(subset="fecha").set_index("fecha").sort_index()
    evi_crudo = df_raw["evi_crudo"].dropna()
    lswi_crudo = df_raw["lswi_crudo"].dropna()
    print("  EVI crudo: {} observaciones, rango {} - {}".format(len(evi_crudo), evi_crudo.index.min().date(), evi_crudo.index.max().date()))
    print("  LSWI crudo: {} observaciones, rango {} - {}".format(len(lswi_crudo), lswi_crudo.index.min().date(), lswi_crudo.index.max().date()))

    # 2. SUAVIZADO
    print("\n2. SUAVIZADO WHITTAKER:")

    # A) Pipeline (indices_suavizados)
    evi_pipe, lswi_pipe = cargar_indices_suavizados_ciclo(id_ciclo, id_parcela)
    evi_pipe_ciclo = evi_pipe.loc[sos:eos].dropna()
    lswi_pipe_ciclo = lswi_pipe.loc[sos:eos].dropna()
    print("  Pipeline (indices_suavizados): EVI={}, LSWI={}".format(len(evi_pipe_ciclo), len(lswi_pipe_ciclo)))

    # B) Notebook re-smooth (sos:eos)
    print("\n  Re-suavizado notebook (sos:eos):")
    dfs_raw_sos = cargar_indices_desde_bd(
        fecha_inicio=str(sos.date()),
        fecha_fin=str(eos.date()),
        ids_parcelas=[id_parcela],
    )
    dfs_vpm_sos = preprocesar_indices_vpm(dfs_raw_sos)
    if col in dfs_vpm_sos["EVI"].columns:
        evi_nb_sos = dfs_vpm_sos["EVI"][col].dropna()
        lswi_nb_sos = dfs_vpm_sos["LSWI"][col].dropna()
        print("  Notebook (sos:eos): EVI={}, LSWI={}".format(len(evi_nb_sos), len(lswi_nb_sos)))
        comparar_series(evi_pipe_ciclo, evi_nb_sos, "  EVI: pipeline vs notebook(sos:eos)")
        comparar_series(lswi_pipe_ciclo, lswi_nb_sos, "  LSWI: pipeline vs notebook(sos:eos)")
    else:
        print("  Columna no encontrada en re-suavizado sos:eos")

    # C) Notebook re-smooth (fecha_inicio:eos)
    print("\n  Re-suavizado notebook (fecha_inicio:eos):")
    dfs_raw_ini = cargar_indices_desde_bd(
        fecha_inicio=str(fecha_inicio.date()),
        fecha_fin=str(eos.date()),
        ids_parcelas=[id_parcela],
    )
    dfs_vpm_ini = preprocesar_indices_vpm(dfs_raw_ini)
    if col in dfs_vpm_ini["EVI"].columns:
        evi_nb_ini = dfs_vpm_ini["EVI"][col].dropna()
        lswi_nb_ini = dfs_vpm_ini["LSWI"][col].dropna()
        print("  Notebook (inicio:eos): EVI={}, LSWI={}".format(len(evi_nb_ini), len(lswi_nb_ini)))
        comparar_series(evi_pipe_ciclo, evi_nb_ini, "  EVI: pipeline vs notebook(inicio:eos)")
        comparar_series(lswi_pipe_ciclo, lswi_nb_ini, "  LSWI: pipeline vs notebook(inicio:eos)")
    else:
        print("  Columna no encontrada en re-suavizado inicio:eos")

    # 3. VPM CON SERIES DEL PIPELINE
    print("\n3. VPM CON SERIES DEL PIPELINE:")
    df_evi_pipe = pd.DataFrame({col: evi_pipe_ciclo})
    df_lswi_pipe = pd.DataFrame({col: lswi_pipe_ciclo})

    lswi_max_serie = float(lswi_pipe_ciclo.max()) if not lswi_pipe_ciclo.empty else None
    if lswi_max_db is not None and lswi_max_serie is not None:
        lswi_max_usado = max(lswi_max_db, lswi_max_serie)
    else:
        lswi_max_usado = lswi_max_db if lswi_max_db is not None else lswi_max_serie

    df_w_scalar_pipe = (1.0 + df_lswi_pipe) / (1.0 + lswi_max_usado) if lswi_max_usado else df_lswi_pipe.copy()
    df_fpar_pipe = 1.0 * df_evi_pipe

    # Clima
    clim_rad = obtener_climatologia("radiacion")
    clim_temp = obtener_climatologia("temperatura")

    dfs_clima_raw = cargar_clima_desde_bd(
        fecha_inicio=str(fecha_inicio.date()),
        fecha_fin=str(eos.date()),
        ids_parcelas=[id_parcela],
    )
    temp_real = dfs_clima_raw["temperature-mean"][col].dropna()
    rad_real = dfs_clima_raw["solar-radiation-flux"][col].dropna()

    fechas_vpm = evi_pipe_ciclo.index
    serie_temp = construir_serie_climatica_prediccion(
        fecha_inicio, eos, temp_real, clim_temp,
    ).reindex(fechas_vpm)
    serie_rad = construir_serie_climatica_prediccion(
        fecha_inicio, eos, rad_real, clim_rad,
    ).reindex(fechas_vpm)

    dfs_veg_pipe = {"EVI": df_evi_pipe, "LSWI": df_lswi_pipe,
                   "FPAR": df_fpar_pipe, "W_scalar": df_w_scalar_pipe}
    dfs_clima_pipe = {"temperature-mean": pd.DataFrame({col: serie_temp}),
                     "solar-radiation-flux": pd.DataFrame({col: serie_rad})}

    dfs_gpp_pipe = calcular_gpp_vpm(dfs_vegetacion=dfs_veg_pipe, dfs_clima=dfs_clima_pipe)
    gpp_pipe = dfs_gpp_pipe["GPP"][col].loc[sos:eos]
    res_pipe = calcular_biomasa_y_rendimiento(gpp_pipe)
    yield_pipe = float(res_pipe["yield_final_tha"].iloc[0]) * 22.0458

    print("  Pipeline VPM:")
    print("    lswi_max_usado={:.4f}".format(lswi_max_usado))
    print("    W_scalar: mean={:.4f}, max={:.4f}".format(df_w_scalar_pipe[col].mean(), df_w_scalar_pipe[col].max()))
    print("    GPP: mean={:.4f}, max={:.4f}, sum={:.2f}".format(gpp_pipe.mean(), gpp_pipe.max(), gpp_pipe.sum()))
    print("    Yield: {:.2f} qq/ha (final={:.2f}, error={:.2f})".format(yield_pipe, rendimiento_final, yield_pipe-rendimiento_final))

    # 4. VPM CON SERIES NOTEBOOK (sos:eos)
    if col in dfs_vpm_sos["EVI"].columns:
        print("\n4. VPM CON SERIES NOTEBOOK (sos:eos):")
        evi_nb = dfs_vpm_sos["EVI"][col].dropna()
        lswi_nb = dfs_vpm_sos["LSWI"][col].dropna()

        lswi_max_nb_serie = float(lswi_nb.max()) if not lswi_nb.empty else None
        if lswi_max_db is not None and lswi_max_nb_serie is not None:
            lswi_max_nb = max(lswi_max_db, lswi_max_nb_serie)
        else:
            lswi_max_nb = lswi_max_db if lswi_max_db is not None else lswi_max_nb_serie

        df_w_nb = (1.0 + pd.DataFrame({col: lswi_nb})) / (1.0 + lswi_max_nb) if lswi_max_nb else pd.DataFrame({col: lswi_nb}).copy()
        df_fpar_nb = 1.0 * pd.DataFrame({col: evi_nb})

        fechas_nb = evi_nb.index
        serie_temp_nb = construir_serie_climatica_prediccion(
            fecha_inicio, eos, temp_real, clim_temp,
        ).reindex(fechas_nb)
        serie_rad_nb = construir_serie_climatica_prediccion(
            fecha_inicio, eos, rad_real, clim_rad,
        ).reindex(fechas_nb)

        dfs_veg_nb = {"EVI": pd.DataFrame({col: evi_nb}), "LSWI": pd.DataFrame({col: lswi_nb}),
                      "FPAR": df_fpar_nb, "W_scalar": df_w_nb}
        dfs_clima_nb = {"temperature-mean": pd.DataFrame({col: serie_temp_nb}),
                        "solar-radiation-flux": pd.DataFrame({col: serie_rad_nb})}

        dfs_gpp_nb = calcular_gpp_vpm(dfs_vegetacion=dfs_veg_nb, dfs_clima=dfs_clima_nb)
        gpp_nb = dfs_gpp_nb["GPP"][col].loc[sos:eos]
        res_nb = calcular_biomasa_y_rendimiento(gpp_nb)
        yield_nb = float(res_nb["yield_final_tha"].iloc[0]) * 22.0458

        print("  Notebook VPM (sos:eos):")
        print("    lswi_max_usado={:.4f}".format(lswi_max_nb))
        print("    W_scalar: mean={:.4f}, max={:.4f}".format(df_w_nb[col].mean(), df_w_nb[col].max()))
        print("    GPP: mean={:.4f}, max={:.4f}, sum={:.2f}".format(gpp_nb.mean(), gpp_nb.max(), gpp_nb.sum()))
        print("    Yield: {:.2f} qq/ha (diff vs pipeline={:.2f})".format(yield_nb, yield_nb-yield_pipe))

        idx_comun = gpp_pipe.index.intersection(gpp_nb.index)
        if len(idx_comun) > 0:
            gpp_p = gpp_pipe.loc[idx_comun].values
            gpp_n = gpp_nb.loc[idx_comun].values
            diff_gpp = gpp_p - gpp_n
            print("    GPP diff: max={:.4f}, mean={:.4f}, rmse={:.4f}".format(
                np.abs(diff_gpp).max(), np.abs(diff_gpp).mean(), np.sqrt(np.mean(diff_gpp**2))))

    # 5. EXTRAPOLACION T1/T2/T3
    print("\n5. EXTRAPOLACION T1/T2/T3:")
    for v in ["T1", "T2", "T3"]:
        dias = {"T1": 30, "T2": 60, "T3": 90}[v]
        fecha_v = sos + timedelta(days=dias)

        dfs_raw_v = cargar_indices_desde_bd(
            fecha_inicio=str(fecha_inicio.date()),
            fecha_fin=str(fecha_v.date()),
            ids_parcelas=[id_parcela],
        )
        dfs_vpm_v = preprocesar_indices_vpm(dfs_raw_v)

        if col not in dfs_vpm_v["EVI"].columns:
            print("  {}: sin datos".format(v))
            continue

        evi_obs = dfs_vpm_v["EVI"][col].dropna()
        lswi_obs = dfs_vpm_v["LSWI"][col].dropna()

        try:
            evi_ext, params_evi = extender_serie_con_curva_parametrica(evi_obs, sos, eos, ventana=v)
            lswi_ext, params_lswi = extender_serie_con_curva_parametrica(lswi_obs, sos, eos, ventana=v)

            df_evi_ext = pd.DataFrame({col: evi_ext})
            df_lswi_ext = pd.DataFrame({col: lswi_ext})

            lswi_max_ext_serie = float(lswi_ext.max()) if not lswi_ext.empty else None
            if lswi_max_db is not None and lswi_max_ext_serie is not None:
                lswi_max_ext = max(lswi_max_db, lswi_max_ext_serie)
            else:
                lswi_max_ext = lswi_max_db if lswi_max_db is not None else lswi_max_ext_serie

            df_w_ext = (1.0 + df_lswi_ext) / (1.0 + lswi_max_ext) if lswi_max_ext else df_lswi_ext.copy()
            df_fpar_ext = 1.0 * df_evi_ext

            fechas_ext = evi_ext.index
            serie_temp_ext = construir_serie_climatica_prediccion(
                fecha_inicio, eos, temp_real, clim_temp,
            ).reindex(fechas_ext)
            serie_rad_ext = construir_serie_climatica_prediccion(
                fecha_inicio, eos, rad_real, clim_rad,
            ).reindex(fechas_ext)

            dfs_veg_ext = {"EVI": df_evi_ext, "LSWI": df_lswi_ext,
                          "FPAR": df_fpar_ext, "W_scalar": df_w_ext}
            dfs_clima_ext = {"temperature-mean": pd.DataFrame({col: serie_temp_ext}),
                            "solar-radiation-flux": pd.DataFrame({col: serie_rad_ext})}

            dfs_gpp_ext = calcular_gpp_vpm(dfs_vegetacion=dfs_veg_ext, dfs_clima=dfs_clima_ext)
            gpp_ext = dfs_gpp_ext["GPP"][col].loc[sos:eos]
            res_ext = calcular_biomasa_y_rendimiento(gpp_ext)
            yield_ext = float(res_ext["yield_final_tha"].iloc[0]) * 22.0458

            r2_evi = params_evi['r2'] if params_evi else 'N/A'
            r2_lswi = params_lswi['r2'] if params_lswi else 'N/A'
            print("  {} (fecha={}): yield={:.2f} qq/ha, R2_EVI={}, R2_LSWI={}".format(
                v, fecha_v.date(), yield_ext, r2_evi, r2_lswi))

        except Exception as e:
            print("  {}: ERROR - {}".format(v, e))

    print("\n" + "="*80)
    print("RESUMEN: Pipeline EOS yield={:.2f} vs Real={:.2f} (error={:.2f})".format(yield_pipe, rendimiento_final, yield_pipe-rendimiento_final))
    print("="*80 + "\n")


if __name__ == "__main__":
    set_db_path(GPKG_PRUEBAS_PATH)
    print("BD: {}".format(get_db_path()))

    ciclos_test = [2615, 2618, 2620, 2622, 2623, 2625, 2626, 2627, 2628]

    for id_ciclo in ciclos_test:
        try:
            with closing(get_connection_raw()) as conn:
                row = conn.execute("SELECT id_parcela FROM produccion_acumulada_ciclo WHERE id_ciclo=?", (id_ciclo,)).fetchone()
            if row:
                test_diagnostico_ciclo(id_ciclo, row[0])
        except Exception as e:
            print("\nERROR en ciclo {}: {}".format(id_ciclo, e))
            import traceback
            traceback.print_exc()