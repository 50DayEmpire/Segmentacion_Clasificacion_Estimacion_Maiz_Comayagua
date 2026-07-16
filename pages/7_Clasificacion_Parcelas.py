# pages/7_Clasificacion_Parcelas.py — Administración: clasificación de parcelas
import streamlit as st
import pandas as pd
from contextlib import closing
from components.mapa_parcelas import render_mapa_parcelas
from components.sidebar_filtros import render_filtros_parcelas
from utils.queries import (
    cargar_parcelas,
    cargar_ciclos_no_finalizados,
    cargar_predicciones_ciclo,
    cargar_indices_suavizados,
)
from config import ANIOS_HISTORICO
from utils.conexionDB import get_connection_raw
from pipeline.modulo_clasificacion import _score_a_label

st.markdown("## 🏷️ Clasificación de Parcelas")
st.markdown(
    "Herramienta administrativa para revisar y gestionar la clasificación de parcelas "
    "agrícolas en el Valle de Comayagua."
)
st.divider()

# Forzar datos frescos en cada recarga
cargar_parcelas.clear()
cargar_ciclos_no_finalizados.clear()
cargar_indices_suavizados.clear()

with st.sidebar:
    filtros = render_filtros_parcelas()
    st.divider()
    anio_clasif = st.select_slider(
        "Año",
        options=["Todos"] + ANIOS_HISTORICO,
        value="Todos",
        key="anio_clasificacion",
    )
    st.divider()
    orden = st.selectbox(
        "Ordenar por",
        ["SOS (más reciente)", "Score (mayor a menor)", "ID parcela"],
        index=0,
    )
    st.divider()
    if st.button("🔄 Limpiar caché", use_container_width=True):
        cargar_parcelas.clear()
        cargar_ciclos_no_finalizados.clear()
        st.rerun()

# ── Mapa ────────────────────────────────────────────────────────────────────
mapa_filtros = {**filtros, "modo_color": "cultivo"}
resultado = render_mapa_parcelas(mapa_filtros)

clicked = (resultado or {}).get("last_object_clicked")
if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
    st.session_state["clasificacion_clicked"] = clicked

st.divider()

# ── Datos ────────────────────────────────────────────────────────────────────
ciclo_sel = filtros.get("ciclo", "primera")
anio_sel = anio_clasif if isinstance(anio_clasif, int) else None
df = cargar_ciclos_no_finalizados(ciclo_sel, anio=anio_sel)

if df.empty:
    if anio_sel:
        st.info(f"No hay ciclos para **{anio_sel} - {ciclo_sel}**.", icon="✅")
    else:
        st.info(f"No hay ciclos no finalizados para la temporada **{ciclo_sel}**.", icon="✅")
    st.stop()

# ── Ordenar ──────────────────────────────────────────────────────────────────
if orden == "Score (mayor a menor)":
    df["_sort"] = df["score_compuesto"].fillna(-1)
    df = df.sort_values("_sort", ascending=False).drop(columns=["_sort"])
elif orden == "ID parcela":
    df = df.sort_values("id_parcela")
else:
    df = df.sort_values("sos", ascending=False, na_position="last")
df = df.reset_index(drop=True)

# ── Helper: badge color ─────────────────────────────────────────────────────
def _color_badge(score):
    if score is None or pd.isna(score) or score < 0:
        return "#95a5a6", "—"
    if score >= 70:
        return "#27ae60", f"{score:.0f}%"
    if score >= 30:
        return "#f39c12", f"{score:.0f}%"
    return "#e74c3c", f"{score:.0f}%"

# ── Resumen ─────────────────────────────────────────────────────────────────
alta = df[df["score_compuesto"].fillna(-1) >= 70]
cols_sup = st.columns(4)
if anio_sel:
    cols_sup[0].metric("Ciclos totales", len(df))
    cols_sup[1].metric("Alta prob. maíz (≥70)", len(alta))
    cols_sup[2].metric("Finalizados", len(df[df["estado_ciclo"] == "finalizado"]))
    cols_sup[3].metric("Activos/Candidatos", len(df[df["estado_ciclo"].isin(["activo", "candidato"])]))
else:
    cols_sup[0].metric("Ciclos sin finalizar", len(df))
    cols_sup[1].metric("Alta prob. maíz (≥70)", len(alta))
    cols_sup[2].metric("Candidatos", len(df[df["estado_ciclo"] == "candidato"]))
    cols_sup[3].metric("Activos", len(df[df["estado_ciclo"] == "activo"]))

st.divider()
if anio_sel:
    st.markdown(f"### Ciclos — {anio_sel} ({ciclo_sel})")
else:
    st.markdown("### Parcelas pendientes de clasificación")

# ── Session state for expand ────────────────────────────────────────────────
card_key = st.session_state.get("clasificacion_card_key")

# ── Grid de cards ───────────────────────────────────────────────────────────
N_COLS = 4

for start in range(0, len(df), N_COLS):
    cols = st.columns(N_COLS)
    for i in range(N_COLS):
        idx = start + i
        if idx >= len(df):
            with cols[i]:
                st.empty()
            continue

        row = df.iloc[idx]
        pid = int(row["id_parcela"])
        id_ciclo = int(row["id_ciclo"])
        score = row.get("score_compuesto")
        cultivo = row.get("cultivo_predicho", "")
        sos = row.get("sos")
        estado = row["estado_ciclo"]
        k = f"card_{id_ciclo}"

        color_bg, label_badge = _color_badge(score)
        sos_str = sos.strftime("%d/%m/%Y") if pd.notna(sos) else "—"
        is_open = (card_key == k)

        with cols[i]:
            st.markdown(
                f"""
                <div style="border:1.5px solid {'#2e7d32' if is_open else '#3a3a3a'};
                            border-radius:10px; padding:14px;
                            background:{'#1b3a1b' if is_open else '#2a2a2a'};
                            margin-bottom:6px;">
                    <div style="display:flex; justify-content:space-between; align-items:start;">
                        <span style="font-size:1.25rem; font-weight:700; color:#f0f0f0;">#{pid} <span style="font-size:.8rem; font-weight:400; color:#aaa;">C#{id_ciclo}</span></span>
                        <span style="background:{color_bg}; color:white;
                                     padding:1px 10px; border-radius:20px;
                                     font-size:.75rem; font-weight:700;
                                     line-height:1.6;">
                            {label_badge}
                        </span>
                    </div>
                    <div style="margin-top:8px; font-size:.82rem; color:#ccc;">
                        🌱 SOS: {sos_str}
                    </div>
                    <div style="margin-top:3px; font-size:.78rem;">
                        <span style="color:{'#81c784' if estado == 'activo' else '#ffb74d'};">
                            {'●' if estado == 'activo' else '○'} {estado.capitalize()}
                        </span>
                    </div>
                    <div style="margin-top:5px; font-weight:500; font-size:.9rem;
                                color:{color_bg if label_badge != '—' else '#aaa'};">
                        {cultivo if cultivo and not pd.isna(cultivo) else '—'}
                    </div>
                    <div style="margin-top:6px; height:5px; background:#444; border-radius:3px;">
                        <div style="height:5px; width:{max(0, score) if score and not pd.isna(score) else 0}%;
                                    background:{color_bg}; border-radius:3px;"></div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if st.button("📋 Detalle", key=f"btn_{id_ciclo}", use_container_width=True):
                if card_key == k:
                    del st.session_state["clasificacion_card_key"]
                else:
                    st.session_state["clasificacion_card_key"] = k
                st.rerun()

# ── Sección expandida (accordion — una a la vez) ───────────────────────────
if card_key and card_key.startswith("card_"):
    id_ciclo_exp = int(card_key.replace("card_", ""))
    det = df[df["id_ciclo"] == id_ciclo_exp]

    if det.empty:
        st.warning("El ciclo ya no está disponible.")
    else:
        det = det.iloc[0]
        pid_exp = int(det["id_parcela"])

        st.divider()
        st.markdown(f"### 📋 Detalle — Parcela #{pid_exp}  ·  Ciclo #{id_ciclo_exp}")

        cols_m = st.columns(5)
        cols_m[0].metric("Parcela", f"#{pid_exp}")
        cols_m[1].metric("Estado", det["estado_ciclo"].capitalize())
        sd = det["sos"]
        cols_m[2].metric("SOS", sd.strftime("%d/%m/%Y") if pd.notna(sd) else "—")
        sc = det.get("score_compuesto")
        cols_m[3].metric("Score", f"{sc:.1f}%" if pd.notna(sc) else "—")
        cols_m[4].metric(
            "Cultivo predicho",
            (det.get("cultivo_predicho") if pd.notna(det.get("cultivo_predicho")) else "—").replace("_", " ").title(),
        )

        # ── Scores por ventana ────────────────────────────────────────────────
        st.markdown("#### Scores por ventana")
        df_preds = cargar_predicciones_ciclo(id_ciclo_exp)
        if df_preds is not None and not df_preds.empty:
            pcols = st.columns(len(df_preds))
            for j, (_, pr) in enumerate(df_preds.iterrows()):
                with pcols[j]:
                    st.metric(
                        pr["ventana"],
                        f"{pr.get('score_compuesto', 0):.0f}%"
                        if pd.notna(pr.get("score_compuesto"))
                        else "—",
                        pr.get("cultivo_predicho")
                        if pd.notna(pr.get("cultivo_predicho"))
                        else None,
                    )
        else:
            st.caption("Sin predicciones registradas para este ciclo.")

        # ── Gráfica EVI ──────────────────────────────────────────────────────
        st.markdown("#### Serie EVI/LSWI suavizada")

        def _chart_evi_lswi(df, idx_col="fecha"):
            import altair as alt
            df_plot = df.melt(id_vars=[idx_col], var_name="indice", value_name="valor")
            color_scale = alt.Scale(
                domain=["evi", "lswi"],
                range=["#2ecc71", "#3498db"],
            )
            return alt.Chart(df_plot).mark_line().encode(
                x=f"{idx_col}:T",
                y="valor:Q",
                color=alt.Color("indice:N", scale=color_scale, title=None),
                tooltip=[f"{idx_col}:T", "valor:Q", "indice:N"],
            ).properties(height=250).interactive()

        df_idx = cargar_indices_suavizados(id_ciclo_exp)
        if df_idx is not None and not df_idx.empty:
            st.altair_chart(_chart_evi_lswi(df_idx), use_container_width=True)
        else:
            try:
                from pipeline.ingesta import cargar_indices_desde_bd
                from pipeline.modulo_vpm import preprocesar_indices_vpm
                dfs = cargar_indices_desde_bd(ids_parcelas=[pid_exp])
                dfs_proc = preprocesar_indices_vpm(dfs)
                df_evi = dfs_proc["EVI"]
                col_evi = f"id_{pid_exp}"
                if col_evi in df_evi.columns:
                    import altair as alt
                    df_plot = df_evi[[col_evi]].reset_index()
                    st.altair_chart(
                        alt.Chart(df_plot).mark_line(color="#2ecc71").encode(
                            x="fecha:T", y=f"{col_evi}:Q",
                            tooltip=["fecha:T", f"{col_evi}:Q"],
                        ).properties(height=250).interactive(),
                        use_container_width=True,
                    )
                else:
                    st.caption("Sin datos EVI disponibles.")
            except Exception:
                st.caption("Sin datos de índices suavizados para este ciclo.")

        # ── Administrar ──────────────────────────────────────────────────────
        st.markdown("#### Administrar clasificación")

        # Asegurar que la columna clasificacion_final existe
        def _asegurar_columna():
            with closing(get_connection_raw()) as conn:
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(produccion_acumulada_ciclo)"
                ).fetchall()]
                if "clasificacion_final" not in cols:
                    conn.execute(
                        "ALTER TABLE produccion_acumulada_ciclo ADD COLUMN clasificacion_final TEXT"
                    )

        _asegurar_columna()

        clasif_actual = det.get("clasificacion_final", "")
        if pd.isna(clasif_actual):
            clasif_actual = ""
        opciones = ["", "Maíz", "Maíz - baja probabilidad", "Otro", "Incierto"]
        idx_def = opciones.index(clasif_actual) if clasif_actual in opciones else 0

        admin_cols = st.columns([1, 1, 2])
        with admin_cols[0]:
            nueva = st.selectbox(
                "Clasificación manual",
                opciones,
                index=idx_def,
                key=f"sel_clasif_{id_ciclo_exp}",
                label_visibility="collapsed",
            )
        with admin_cols[1]:
            if st.button("💾 Guardar", key=f"save_{id_ciclo_exp}", use_container_width=True):
                try:
                    with closing(get_connection_raw()) as conn:
                        with conn:
                            conn.execute(
                                "UPDATE produccion_acumulada_ciclo "
                                "SET clasificacion_final = ? WHERE id_ciclo = ?",
                                (nueva if nueva else None, id_ciclo_exp),
                            )
                    st.success("Clasificación guardada.")
                    cargar_ciclos_no_finalizados.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al guardar: {e}")
        with admin_cols[2]:
            if st.button(
                "🔄 Recalcular con pipeline",
                key=f"recalc_{id_ciclo_exp}",
                use_container_width=True,
            ):
                with st.spinner("Calculando clasificación..."):
                    try:
                        from pipeline.modulo_clasificacion import (
                            clasificar_parcela_actual,
                            persistir_clasificacion_v2,
                        )
                        from pipeline.ingesta import cargar_indices_desde_bd

                        sos_fecha = det["sos"]
                        dfs = cargar_indices_desde_bd(ids_parcelas=[pid_exp])
                        df_evi = dfs["EVI"]

                        with closing(get_connection_raw()) as conn:
                            res = clasificar_parcela_actual(
                                conn, pid_exp, sos_fecha, df_evi
                            )

                        if res["estado"] == "evaluado":
                            with closing(get_connection_raw()) as conn:
                                persistir_clasificacion_v2(
                                    conn, res, id_ciclo_exp, ventana="T3",
                                )

                            score_calc = res.get("score_compuesto", 0)
                            st.success(
                                f"Recalculado: **{_score_a_label(score_calc) if pd.notna(score_calc) else 'Incierto'}** "
                                f"(score: {score_calc:.1f}%, "
                                f"patrón: {res.get('patron_usado', '—')})"
                            )
                        else:
                            st.warning(
                                "No se pudo evaluar: "
                                + res.get("motivo", "fuera de ventana o sin datos")
                            )
                    except Exception as e:
                        st.error(f"Error en recálculo: {e}")

                cargar_ciclos_no_finalizados.clear()
                st.rerun()
