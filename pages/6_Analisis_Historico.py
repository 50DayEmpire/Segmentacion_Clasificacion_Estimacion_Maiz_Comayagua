# pages/6_Analisis_Historico.py — Análisis histórico multianual
import pandas as pd
import streamlit as st
from contextlib import closing
from datetime import timedelta
from components.mapa_parcelas import render_mapa_parcelas
from components.sidebar_filtros import render_filtros_historico
from utils.queries import cargar_parcelas, cargar_ciclos_historicos, cargar_datos_series
from components.graficas_series import _figura_series
from config import DIAS_VENTANAS
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

col_mapa, col_detalle = st.columns([3, 1], gap="medium")

with col_mapa:
    mapa_filtros = {**filtros, "modo_color": "cultivo"}
    resultado = render_mapa_parcelas(mapa_filtros)

with col_detalle:
    st.markdown("#### Detalle de parcela")
    clicked = (resultado or {}).get("last_object_clicked")
    if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
        st.session_state["historico_clicked"] = clicked
    clicked = st.session_state.get("historico_clicked")

    if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
        props = clicked
        for k, v in props.items():
            if k.startswith("_"):
                continue
            st.markdown(
                f"<div style='display:flex; justify-content:space-between; "
                f"padding:.25rem 0; border-bottom:1px solid #2d3139; "
                f"font-size:.9rem;'>"
                f"<span style='color:#95a5a6;'>{k}</span>"
                f"<span style='font-weight:600;'>{v}</span></div>",
                unsafe_allow_html=True,
            )
        conteo = sum(1 for k in props if not k.startswith("_"))
        if conteo <= 1 and "id_parcela" in props:
            try:
                gdf = cargar_parcelas()
                pid = int(props["id_parcela"])
                fila = gdf[gdf["id_parcela"] == pid]
                if not fila.empty:
                    area_ha = fila.iloc[0].get("area_ha")
                    area_m2 = fila.iloc[0].get("area_m2")
                    if area_ha is not None:
                        st.metric("Área (ha)", f"{area_ha:.4f}")
                    if area_m2 is not None:
                        st.metric("Área (m²)", f"{area_m2:.2f}")
            except Exception:
                pass
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
        ca, cb, cc, cd, ce = st.columns(5)
        ca.metric("Ciclo", f"#{ciclo['id_ciclo']}")
        cb.metric("SOS", ciclo["sos"].strftime("%d/%m/%Y") if pd.notna(ciclo["sos"]) else "—")
        cc.metric("EOS", ciclo["eos"].strftime("%d/%m/%Y") if pd.notna(ciclo["eos"]) else "—")
        cd.metric("Rendimiento", f"{ciclo['rendimiento']:.1f} qq/ha" if pd.notna(ciclo.get("rendimiento")) else "—")
        ce.metric("Producción Estimada de la Parcela", f"{ciclo['producción_total']:.1f} qq" if pd.notna(ciclo.get("producción_total")) else "—")

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
        st.markdown(f"#### Predicción ventana {ventana}")
        id_prediccion = None
        if not fila_pred.empty:
            p = fila_pred.iloc[0]
            id_prediccion = p["id_prediccion"]
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
            for tipo in ("raw", "smoothed"):
                for idx in ("EVI", "LSWI"):
                    serie = datos_series[tipo].get(idx)
                    if serie is not None and not serie.empty:
                        datos_ciclo[tipo][idx] = serie.loc[sos:fecha_limite]
                    else:
                        datos_ciclo[tipo][idx] = serie

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

            fig = _figura_series(
                f"Parcela {id_parcela_click} — Ciclo #{id_ciclo}",
                datos_ciclo,
                ["EVI", "LSWI"],
                ventana_fecha=fecha_limite,
                ventana_nombre=ventana,
                extrapolado=extrapolado,
                validacion=validacion,
            )
            st.plotly_chart(fig, use_container_width=True)

    else:
        st.info(f"No hay ciclos registrados para la parcela **{id_parcela_click}** en {temporada} {anio}.")
else:
    st.info("Haz clic en una parcela del mapa para ver sus ciclos históricos.", icon="🗂️")
