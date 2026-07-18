# pages/1_Parcelas.py — Vista de mapa interactivo de parcelas
import pandas as pd
import streamlit as st
from contextlib import closing
from datetime import timedelta
from components.mapa_parcelas import render_mapa_parcelas
from components.sidebar_filtros import render_filtros_parcelas
from utils.queries import cargar_parcelas, cargar_ciclos_historicos, cargar_datos_series
from components.graficas_series import _figura_series
from config import DIAS_VENTANAS, DURACION_MAX_CICLO
from utils.conexionDB import get_connection_raw

# ── Título ─────────────────────────────────────────────────────────────────────
st.markdown("## 🗺️ Observatorio")
st.markdown(
    "Mapa interactivo de parcelas agrícolas en el Valle de Comayagua. "
    "Colorea por **cultivo clasificado** o por **rendimiento estimado** (qq/ha)."
)
st.divider()

# ── Filtros en el sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    filtros = render_filtros_parcelas()
    st.divider()
    if st.button("🔄 Limpiar caché", use_container_width=True, help="Recarga los datos desde la base de datos"):
        cargar_parcelas.clear()
        st.rerun()

# ── Mapa a ancho completo ─────────────────────────────────────────────────────
resultado = render_mapa_parcelas(filtros)

# ── Detalle de parcela debajo del mapa ────────────────────────────────────────
clicked = (resultado or {}).get("last_object_clicked")
if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
    st.session_state["parcelas_clicked"] = clicked

clicked = st.session_state.get("parcelas_clicked")
if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
    props = clicked
    pid = int(props.get("id_parcela", 0))

    area_ha = None
    area_m2 = None
    try:
        gdf = cargar_parcelas()
        fila = gdf[gdf["id_parcela"] == pid]
        if not fila.empty:
            area_ha = fila.iloc[0].get("area_ha")
            area_m2 = fila.iloc[0].get("area_m2")
    except Exception:
        pass

    st.divider()
    cols_fila = [c for c in [1, 1 if area_ha is not None else 0, 1 if area_m2 is not None else 0, 1] if c > 0]
    cols = st.columns(cols_fila if cols_fila else [1])
    idx = 0
    with cols[idx]:
        st.markdown(
            f"<div style='font-size:2.2rem; font-weight:700;'>#{pid}</div>"
            f"<div style='font-size:1rem; color:#95a5a6;'>Parcela</div>",
            unsafe_allow_html=True,
        )
    idx += 1
    if area_ha is not None:
        with cols[idx]:
            st.markdown(
                f"<div style='font-size:2.2rem; font-weight:700; line-height:1.2;'>{area_ha:.2f}</div>"
                f"<div style='font-size:.85rem; color:#95a5a6;'>hectáreas</div>",
                unsafe_allow_html=True,
            )
        idx += 1
        with cols[idx]:
            st.markdown(
                f"<div style='font-size:2.2rem; font-weight:700; line-height:1.2;'>{area_m2:.0f}</div>"
                f"<div style='font-size:.85rem; color:#95a5a6;'>m²</div>",
                unsafe_allow_html=True,
            )
        idx += 1
    nombre_cultivo = props.get("cultivo", props.get("Cultivo", ""))
    if nombre_cultivo:
        with cols[idx]:
            st.markdown(
                f"<div style='font-size:1.3rem; font-weight:500; padding-top:.6rem;'>{nombre_cultivo}</div>",
                unsafe_allow_html=True,
            )

    items = [(k, v) for k, v in props.items()
             if not k.startswith("_") and k.lower() not in ("id_parcela", "cultivo", "area_ha", "area_m2")]
    if items:
        st.markdown("&nbsp;")
        cols_2 = st.columns(len(items))
        for i, (k, v) in enumerate(items):
            cols_2[i].metric(k, str(v))

    st.divider()

    # ── Ciclo, predicción y series temporales ────────────────────────────
    ciclo = filtros["ciclo"]
    ventana = filtros["ventana"]

    id_parcela_click = pid
    df_ciclos = cargar_ciclos_historicos(anio=2026, temporada=ciclo, id_parcela=id_parcela_click)
    if not df_ciclos.empty:
        st.markdown(f"### 📋 Ciclo de parcela **{id_parcela_click}** — {ciclo.capitalize()} 2026")
        if len(df_ciclos) > 1:
            opciones = {
                f"#{row['id_ciclo']} — SOS {row['sos'].strftime('%d/%m/%Y')} → EOS {row['eos'].strftime('%d/%m/%Y')}": row["id_ciclo"]
                for _, row in df_ciclos.iterrows()
            }
            etiqueta = st.selectbox("Selecciona un ciclo", options=list(opciones.keys()), key="ciclo_selector")
            id_ciclo = opciones[etiqueta]
        else:
            id_ciclo = df_ciclos.iloc[0]["id_ciclo"]

        ciclo_row = df_ciclos[df_ciclos["id_ciclo"] == id_ciclo].iloc[0]
        ca, cb, cc, cd, ce, cf = st.columns(6)
        ca.metric("Ciclo", f"#{ciclo_row['id_ciclo']}")
        cb.metric("SOS", ciclo_row["sos"].strftime("%d/%m/%Y") if pd.notna(ciclo_row["sos"]) else "—")
        cc.metric("EOS", ciclo_row["eos"].strftime("%d/%m/%Y") if pd.notna(ciclo_row["eos"]) else "—")
        cd.metric("Duración del Ciclo", f"{(ciclo_row['eos'] - ciclo_row['sos']).days} días" if pd.notna(ciclo_row.get("eos")) and pd.notna(ciclo_row.get("sos")) else "—")
        ce.metric("Rendimiento", f"{ciclo_row['rendimiento']:.1f} qq/ha" if pd.notna(ciclo_row.get("rendimiento")) else "—")
        cf.metric("Producción Estimada de la Parcela", f"{ciclo_row['produccion_total']:.1f} qq" if pd.notna(ciclo_row.get("produccion_total")) else "—")

        if pd.notna(ciclo_row.get("sos")) and pd.notna(ciclo_row.get("eos")):
            duracion = (ciclo_row["eos"] - ciclo_row["sos"]).days
            if duracion > DURACION_MAX_CICLO:
                st.warning(f"⚠️ Ciclo anormalmente largo ({duracion} días). "
                           f"La duración máxima esperada es {DURACION_MAX_CICLO} días.")

        # ── Predicción ventana ───────────────────────────────────────────
        with closing(get_connection_raw()) as conn:
            df_pred = pd.read_sql("""
                SELECT id_prediccion, ventana, fecha_ventana,
                       gpp_acumulado, npp_acumulado,
                       rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela
                FROM predicciones_ventana
                WHERE id_ciclo = ?
                ORDER BY ventana
            """, conn, params=(int(id_ciclo),), parse_dates=["fecha_ventana"])
        fila_pred = df_pred[df_pred["ventana"] == ventana]
        id_prediccion = None
        sin_curva_evi = False
        sin_curva_lswi = False
        if not fila_pred.empty:
            p = fila_pred.iloc[0]
            id_prediccion = p["id_prediccion"]
            with closing(get_connection_raw()) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n_ext, "
                    "SUM(CASE WHEN evi_extrapolado IS NOT NULL THEN 1 ELSE 0 END) AS n_evi, "
                    "SUM(CASE WHEN lswi_extrapolado IS NOT NULL THEN 1 ELSE 0 END) AS n_lswi "
                    "FROM series_extrapoladas_ventana WHERE id_prediccion = ?",
                    (int(id_prediccion),),
                ).fetchone()
                n_evi = row[1] or 0
                n_lswi = row[2] or 0
            sin_curva_evi = n_evi == 0 and ventana != "EOS"
            sin_curva_lswi = n_lswi == 0 and ventana != "EOS"

        if sin_curva_evi or sin_curva_lswi:
            with st.container(horizontal=True, vertical_alignment="center", gap="xxsmall"):
                st.markdown(f"#### Predicción ventana {ventana}")
                if sin_curva_evi:
                    st.button("⚠️", disabled=True, help="Producción estimada parcial, no fue posible ajustar una curva al ciclo EVI")
                if sin_curva_lswi:
                    st.button("⚠️", disabled=True, help="Sin producción estimada, no fue posible ajustar una curva al ciclo LSWI")
        else:
            st.markdown(f"#### Predicción ventana {ventana}")
        if not fila_pred.empty:
            ca, cb, cc, cd = st.columns(4)
            ca.metric("GPP acumulado", f"{p['gpp_acumulado']:.2f}" if pd.notna(p.get("gpp_acumulado")) else "—")
            cb.metric("NPP acumulado", f"{p['npp_acumulado']:.2f}" if pd.notna(p.get("npp_acumulado")) else "—")
            cc.metric("Rend. estimado", f"{p['rendimiento_estimado_qq_ha']:.1f} qq/ha" if pd.notna(p.get("rendimiento_estimado_qq_ha")) else "—")
            cd.metric("Producción Total Parcela", f"{p['rendimiento_estimado_qq_parcela']:.1f} qq" if pd.notna(p.get("rendimiento_estimado_qq_parcela")) else "—")
        else:
            st.caption("No hay predicción registrada para esta ventana.")

        # ── Series temporales ────────────────────────────────────────────
        st.divider()
        st.markdown("### 📈 Series temporales EVI y LSWI")
        sos = ciclo_row["sos"]
        eos = ciclo_row["eos"]
        if ventana == "EOS":
            fecha_limite = eos
        else:
            fecha_limite = sos + timedelta(days=DIAS_VENTANAS.get(ventana, 0))
        datos_series = cargar_datos_series(id_parcela_click)
        if datos_series is not None and pd.notna(sos) and pd.notna(eos):
            datos_ciclo = {"raw": {}, "smoothed": {}}
            for idx in ("EVI", "LSWI"):
                serie_raw = datos_series["raw"].get(idx)
                if serie_raw is not None and not serie_raw.empty:
                    datos_ciclo["raw"][idx] = serie_raw.loc[sos:fecha_limite]
                else:
                    datos_ciclo["raw"][idx] = serie_raw

            if ventana != "EOS" and datos_ciclo["raw"].get("EVI") is not None and datos_ciclo["raw"].get("LSWI") is not None:
                from pipeline.modulo_vpm import preprocesar_indices_vpm
                col_name = f"id_{id_parcela_click}"
                df_evi_raw_recortado = pd.DataFrame({col_name: datos_ciclo["raw"]["EVI"]})
                df_lswi_raw_recortado = pd.DataFrame({col_name: datos_ciclo["raw"]["LSWI"]})
                dfs_crudos_recortados = {"EVI": df_evi_raw_recortado, "LSWI": df_lswi_raw_recortado}
                try:
                    dfs_suave_recortados = preprocesar_indices_vpm(dfs_crudos_recortados)
                    datos_ciclo["smoothed"]["EVI"] = dfs_suave_recortados["EVI"][col_name]
                    datos_ciclo["smoothed"]["LSWI"] = dfs_suave_recortados["LSWI"][col_name]
                except Exception:
                    for idx in ("EVI", "LSWI"):
                        serie_smooth = datos_series["smoothed"].get(idx)
                        if serie_smooth is not None and not serie_smooth.empty:
                            datos_ciclo["smoothed"][idx] = serie_smooth.loc[sos:fecha_limite]
                        else:
                            datos_ciclo["smoothed"][idx] = serie_smooth
            else:
                for idx in ("EVI", "LSWI"):
                    serie_smooth = datos_series["smoothed"].get(idx)
                    if serie_smooth is not None and not serie_smooth.empty:
                        datos_ciclo["smoothed"][idx] = serie_smooth.loc[sos:fecha_limite]
                    else:
                        datos_ciclo["smoothed"][idx] = serie_smooth

            extrapolado = None
            if id_prediccion is not None:
                with closing(get_connection_raw()) as conn:
                    df_ext = pd.read_sql("""
                        SELECT fecha, evi_extrapolado, lswi_extrapolado
                        FROM series_extrapoladas_ventana
                        WHERE id_prediccion = ?
                        ORDER BY fecha
                    """, conn, params=(int(id_prediccion),), parse_dates=["fecha"])
                if not df_ext.empty:
                    raw_ext = {}
                    if df_ext["evi_extrapolado"].notna().any():
                        raw_ext["EVI"] = df_ext.set_index("fecha")["evi_extrapolado"].dropna()
                    if df_ext["lswi_extrapolado"].notna().any():
                        raw_ext["LSWI"] = df_ext.set_index("fecha")["lswi_extrapolado"].dropna()
                    if raw_ext:
                        extrapolado = {}
                        for idx in ("EVI", "LSWI"):
                            smooth = datos_ciclo["smoothed"].get(idx)
                            ext = raw_ext.get(idx)
                            if ext is not None and smooth is not None and not smooth.empty:
                                last_smooth = smooth.iloc[-1]
                                extrapolado[idx] = pd.concat([
                                    pd.Series([last_smooth], index=[smooth.index[-1]]),
                                    ext,
                                ])
                            elif ext is not None:
                                extrapolado[idx] = ext

            validacion = {}
            for idx in ("EVI", "LSWI"):
                serie = datos_series["smoothed"].get(idx)
                if serie is not None and not serie.empty:
                    tramo = serie.loc[fecha_limite:eos]
                    if not tramo.empty:
                        validacion[idx] = tramo

            sos_ts = pd.Timestamp(sos) if pd.notna(sos) else None
            eos_ts = pd.Timestamp(eos) if pd.notna(eos) else None

            fig = _figura_series(
                f"Parcela {id_parcela_click} — Ciclo #{id_ciclo}",
                datos_ciclo,
                ["EVI", "LSWI"],
                ventana_fecha=fecha_limite,
                ventana_nombre=ventana,
                extrapolado=extrapolado,
                validacion=validacion,
                sos_fecha=sos_ts,
                eos_fecha=eos_ts,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No hay datos de series temporales disponibles para esta parcela.")
    else:
        st.info(f"No hay ciclos registrados para la parcela **{id_parcela_click}** en {ciclo} 2026.")

    st.markdown("---")
    if st.button(f"📈 Ver Series Temporales — parcela {pid}", use_container_width=True):
        st.session_state["parcela_series"] = pid
        st.switch_page("pages/2_Series_Temporales.py")
else:
    st.info(
        "Haz clic sobre una parcela en el mapa para ver su información.",
        icon="👆",
    )
