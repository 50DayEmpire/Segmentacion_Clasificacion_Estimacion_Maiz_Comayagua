# pages/6_Analisis_Historico.py — Análisis histórico multianual
import pandas as pd
import streamlit as st
from contextlib import closing
from datetime import timedelta
from components.mapa_parcelas import render_mapa_parcelas
from components.sidebar_filtros import render_filtros_historico
from utils.queries import cargar_parcelas, cargar_ciclos_historicos, cargar_datos_series
from components.graficas_series import _figura_series
from config import DIAS_VENTANAS, DURACION_MAX_CICLO
from pipeline.flujos_trabajo import recalcular_en_memoria
from utils.conexionDB import get_connection_raw

st.markdown("## 📊 Análisis Histórico")
st.markdown(
    "Exploración multianual de parcelas agrícolas en el Valle de Comayagua. "
    "Haz clic en una parcela del mapa para ver sus ciclos y predicciones."
)
st.divider()

with st.sidebar:
    filtros = render_filtros_historico()
    st.divider()
    if st.button("🔄 Limpiar caché", use_container_width=True, help="Recarga los datos desde la base de datos"):
        cargar_parcelas.clear()
        cargar_ciclos_historicos.clear()
        st.rerun()

anio = filtros["anio"]
temporada = filtros["ciclo"]

mapa_filtros = {**filtros, "modo_color": "cultivo"}
resultado = render_mapa_parcelas(mapa_filtros)

clicked = (resultado or {}).get("last_object_clicked")
if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
    st.session_state["historico_clicked"] = clicked
clicked = st.session_state.get("historico_clicked")

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

    cols_fila = []
    ancho_pid = 1
    ancho_area = 1 if area_ha is not None else 0
    ancho_cultivo = 1
    cols_fila = [ancho_pid, ancho_area, ancho_area, ancho_cultivo]
    cols_fila = [c for c in cols_fila if c > 0]

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
        cols_2 = st.columns(len(items))
        for i, (k, v) in enumerate(items):
            cols_2[i].metric(k, str(v))
else:
    st.info("Haz clic sobre una parcela en el mapa.", icon="👆")

st.divider()

id_parcela_click = None
clicked = st.session_state.get("historico_clicked")
if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
    try:
        id_parcela_click = int(clicked["id_parcela"])
    except (ValueError, TypeError):
        pass

if id_parcela_click is not None:
    df_ciclos = cargar_ciclos_historicos(anio=anio, temporada=temporada, id_parcela=id_parcela_click)
    if not df_ciclos.empty:
        st.markdown(f"### 📋 Ciclos de parcela **{id_parcela_click}** — {temporada.capitalize()} {anio}")
        if len(df_ciclos) > 1:
            opciones = {
                f"#{row['id_ciclo']} — SOS {row['sos'].strftime('%d/%m/%Y')} → EOS {row['eos'].strftime('%d/%m/%Y')}": row["id_ciclo"]
                for _, row in df_ciclos.iterrows()
            }
            etiqueta = st.selectbox("Selecciona un ciclo", options=list(opciones.keys()), key="ciclo_selector")
            id_ciclo = opciones[etiqueta]
        else:
            id_ciclo = df_ciclos.iloc[0]["id_ciclo"]

        ciclo = df_ciclos[df_ciclos["id_ciclo"] == id_ciclo].iloc[0]
        ca, cb, cc, cd, ce, cf = st.columns(6)
        ca.metric("Ciclo", f"#{ciclo['id_ciclo']}")
        cb.metric("SOS", ciclo["sos"].strftime("%d/%m/%Y") if pd.notna(ciclo["sos"]) else "—")
        cc.metric("EOS", ciclo["eos"].strftime("%d/%m/%Y") if pd.notna(ciclo["eos"]) else "—")
        cd.metric("Duración del Ciclo", f"{(ciclo['eos'] - ciclo['sos']).days} días" if pd.notna(ciclo.get("eos")) and pd.notna(ciclo.get("sos")) else "—")
        ce.metric("Rendimiento", f"{ciclo['rendimiento']:.1f} qq/ha" if pd.notna(ciclo.get("rendimiento")) else "—")
        cf.metric("Producción Estimada de la Parcela", f"{ciclo['produccion_total']:.1f} qq" if pd.notna(ciclo.get("produccion_total")) else "—")

        if pd.notna(ciclo.get("sos")) and pd.notna(ciclo.get("eos")):
            duracion = (ciclo["eos"] - ciclo["sos"]).days
            if duracion > DURACION_MAX_CICLO:
                st.warning(f"⚠️ Ciclo anormalmente largo ({duracion} días). "
                           f"La duración máxima esperada es {DURACION_MAX_CICLO} días.")

        ventana = filtros["ventana"]
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
                    st.button(
                        "⚠️",
                        disabled=True,
                        help="Producción estimada parcial, no fue posible ajustar una curva al ciclo EVI",
                    )
                if sin_curva_lswi:
                    st.button(
                        "⚠️",
                        disabled=True,
                        help="Sin producción estimada, no fue posible ajustar una curva al ciclo LSWI",
                    )
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

        st.divider()
        st.markdown("### 📈 Series temporales EVI y LSWI")
        sos = ciclo["sos"]
        eos = ciclo["eos"]
        if ventana == "EOS":
            fecha_limite = eos
        else:
            fecha_limite = sos + timedelta(days=DIAS_VENTANAS.get(ventana, 0))
        datos_series = cargar_datos_series(id_parcela_click)
        if datos_series is not None and pd.notna(sos) and pd.notna(eos):
            datos_ciclo = {"raw": {}, "smoothed": {}}
            
            # 1. Poblar y recortar las series crudas (raw)
            for idx in ("EVI", "LSWI"):
                serie_raw = datos_series["raw"].get(idx)
                if serie_raw is not None and not serie_raw.empty:
                    datos_ciclo["raw"][idx] = serie_raw.loc[sos:fecha_limite]
                else:
                    datos_ciclo["raw"][idx] = serie_raw
            
            # 2. Calcular el suavizado local en ventanas predictivas (T1, T2, T3) para replicar al backend
            if ventana != "EOS" and datos_ciclo["raw"].get("EVI") is not None and datos_ciclo["raw"].get("LSWI") is not None:
                from pipeline.modulo_vpm import preprocesar_indices_vpm
                
                col_name = f"id_{id_parcela_click}"
                df_evi_raw_recortado = pd.DataFrame({col_name: datos_ciclo["raw"]["EVI"]})
                df_lswi_raw_recortado = pd.DataFrame({col_name: datos_ciclo["raw"]["LSWI"]})
                
                dfs_crudos_recortados = {
                    "EVI": df_evi_raw_recortado,
                    "LSWI": df_lswi_raw_recortado,
                }
                
                try:
                    # Ejecutar el suavizado local idéntico al motor predictivo
                    dfs_suave_recortados = preprocesar_indices_vpm(
                        dfs_crudos_recortados
                    )
                    datos_ciclo["smoothed"]["EVI"] = dfs_suave_recortados["EVI"][col_name]
                    datos_ciclo["smoothed"]["LSWI"] = dfs_suave_recortados["LSWI"][col_name]
                except Exception:
                    # Fallback al suavizado global recortado en caso de error
                    for idx in ("EVI", "LSWI"):
                        serie_smooth = datos_series["smoothed"].get(idx)
                        if serie_smooth is not None and not serie_smooth.empty:
                            datos_ciclo["smoothed"][idx] = serie_smooth.loc[sos:fecha_limite]
                        else:
                            datos_ciclo["smoothed"][idx] = serie_smooth
            else:
                # Ventana EOS o fallback general: usar suavizado global recortado
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

            # ── Toggle modo ajuste ─────────────────────────────────────
            # Debe ir antes del chart para que el dragmode se aplique
            modo_ajuste = st.checkbox(
                "✏️ Activar selección visual de SOS/EOS",
                value=st.session_state.get("ajuste_modo_activo", False),
                help="Activa la herramienta de selección en el gráfico para arrastrar un rectángulo sobre el rango deseado del ciclo.",
            )
            st.session_state["ajuste_modo_activo"] = modo_ajuste

            if modo_ajuste:
                fig.update_layout(dragmode="select", hovermode=False)
                st.info(
                    "🖱️ Arrastra un rectángulo sobre el gráfico, desde el SOS propuesto hasta el EOS propuesto. "
                    "Luego abre el panel **✏️ Ajustar límites del ciclo** debajo del gráfico para ver las fechas seleccionadas.",
                    icon="ℹ️",
                )
            else:
                fig.update_layout(dragmode="zoom", hovermode="x unified")

            evento = st.plotly_chart(fig, use_container_width=True, on_select="rerun")

            # ── Panel de ajuste visual de límites SOS/EOS ─────────────────
            # Limpiar estado si cambió el ciclo seleccionado
            if st.session_state.get("ajuste_id_ciclo") != id_ciclo:
                for k in list(st.session_state.keys()):
                    if k.startswith("ajuste_"):
                        st.session_state.pop(k, None)
                st.session_state["ajuste_id_ciclo"] = id_ciclo

            with st.expander("✏️ Ajustar límites del ciclo", expanded=modo_ajuste):
                # Capturar selección del gráfico (desde box o desde points)
                evento_seleccion = None
                if evento is not None:
                    # Streamlit >=1.36 devuelve un objeto con .selection (dict-like)
                    evento_seleccion = getattr(evento, "selection", None) or evento.get("selection", None) if isinstance(evento, dict) else None
                if evento_seleccion:
                    xr = []
                    box = evento_seleccion.get("box", evento_seleccion.get("Box", None))
                    if box is not None:
                        if isinstance(box, list) and len(box):
                            xr = box[0].get("xrange", box[0].get("x", []))
                        elif isinstance(box, dict):
                            xr = box.get("xrange", box.get("x", []))
                    if not xr:
                        pts = evento_seleccion.get("points", [])
                        if pts:
                            xs = [p.get("x") for p in pts if p.get("x") is not None]
                            if xs:
                                xr = [min(xs), max(xs)]
                    if len(xr) == 2:
                        st.session_state["ajuste_nuevo_sos"] = pd.Timestamp(xr[0]).date()
                        st.session_state["ajuste_nuevo_eos"] = pd.Timestamp(xr[1]).date()

                sos_val = sos if hasattr(sos, "date") else sos
                eos_val = eos if hasattr(eos, "date") else eos
                default_sos = st.session_state.get("ajuste_nuevo_sos", sos_val)
                default_eos = st.session_state.get("ajuste_nuevo_eos", eos_val)

                col1, col2 = st.columns(2)
                nuevo_sos = col1.date_input("SOS", value=default_sos)
                nuevo_eos = col2.date_input("EOS", value=default_eos)
                dias_prop = (nuevo_eos - nuevo_sos).days
                st.caption(f"Duración propuesta: {dias_prop} días" + (" ⚠️ Anormalmente largo" if dias_prop > DURACION_MAX_CICLO else ""))

                st.session_state["ajuste_nuevo_sos"] = nuevo_sos
                st.session_state["ajuste_nuevo_eos"] = nuevo_eos

                col_a, col_b, col_c = st.columns(3)
                hay_rec = st.session_state.get("ajuste_activo", False)

                if col_a.button("🔄 Recalcular", use_container_width=True):
                    with st.spinner("Recalculando producción…"):
                        res = recalcular_en_memoria(
                            id_ciclo=int(id_ciclo),
                            nuevo_sos=nuevo_sos,
                            nuevo_eos=nuevo_eos,
                        )
                    if res:
                        st.session_state["ajuste_resultado"] = res
                        st.session_state["ajuste_activo"] = True
                        st.rerun()
                    else:
                        st.error("No se pudo recalcular. Verifica que existan datos EVI/LSWI para el rango seleccionado.")

                if col_b.button("💾 Guardar", use_container_width=True, disabled=not hay_rec):
                    with closing(get_connection_raw()) as conn:
                        with conn:
                            conn.execute("""
                                DELETE FROM series_extrapoladas_ventana
                                WHERE id_prediccion IN (
                                    SELECT id_prediccion FROM predicciones_ventana WHERE id_ciclo = ?
                                )
                            """, (int(id_ciclo),))
                            conn.execute("DELETE FROM predicciones_ventana WHERE id_ciclo = ?", (int(id_ciclo),))
                            t1 = nuevo_sos + timedelta(days=DIAS_VENTANAS["T1"])
                            t2 = nuevo_sos + timedelta(days=DIAS_VENTANAS["T2"])
                            t3 = nuevo_sos + timedelta(days=DIAS_VENTANAS["T3"])
                            conn.execute("""
                                UPDATE produccion_acumulada_ciclo
                                SET sos = ?, eos = ?, t1 = ?, t2 = ?, t3 = ?
                                WHERE id_ciclo = ?
                            """, (str(nuevo_sos), str(nuevo_eos),
                                  str(t1), str(t2), str(t3), int(id_ciclo)))
                            r = st.session_state["ajuste_resultado"]
                            conn.execute("""
                                INSERT INTO predicciones_ventana
                                    (id_ciclo, id_parcela, ventana, fecha_ventana,
                                     lswi_max_efectivo_usado, gpp_acumulado, npp_acumulado,
                                     rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela,
                                     fecha_congelamiento)
                                VALUES (?, ?, 'EOS', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """, (int(id_ciclo), int(id_parcela_click), str(nuevo_eos),
                                  r.get("gpp_acumulado"), r.get("gpp_acumulado"),
                                  r.get("npp_acumulado"),
                                  r["yield_qq_ha"], r["yield_qq_parcela"]))
                            conn.execute("""
                                UPDATE produccion_acumulada_ciclo
                                SET rendimiento = ?, produccion_total = ?
                                WHERE id_ciclo = ?
                            """, (r["yield_qq_ha"], r["yield_qq_parcela"], int(id_ciclo)))
                    for k in list(st.session_state.keys()):
                        if k.startswith("ajuste_"):
                            st.session_state.pop(k, None)
                    st.success("✅ Límites y producción guardados correctamente.")
                    st.cache_data.clear()
                    st.rerun()

                if col_c.button("❌ Cancelar", use_container_width=True, disabled=not hay_rec):
                    for k in list(st.session_state.keys()):
                        if k.startswith("ajuste_"):
                            st.session_state.pop(k, None)
                    st.info("↩️ Cambios descartados.")
                    st.rerun()

                if hay_rec:
                    r = st.session_state["ajuste_resultado"]
                    orig_rend = ciclo.get("rendimiento")
                    orig_prod = ciclo.get("produccion_total")
                    st.markdown("##### Comparativa")
                    ca2, cb2, cc2, cd2 = st.columns(4)
                    ca2.metric("Rend. actual", f"{orig_rend:.1f} qq/ha" if pd.notna(orig_rend) else "—")
                    cb2.metric("Rend. recalculado", f"{r['yield_qq_ha']:.1f} qq/ha")
                    cc2.metric("Prod. actual", f"{orig_prod:.1f} qq" if pd.notna(orig_prod) else "—")
                    cd2.metric("Prod. recalculada", f"{r['yield_qq_parcela']:.1f} qq")

    else:
        st.info(f"No hay ciclos registrados para la parcela **{id_parcela_click}** en {temporada} {anio}.")
else:
    st.info("Haz clic en una parcela del mapa para ver sus ciclos históricos.", icon="🗂️")
