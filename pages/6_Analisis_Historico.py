# pages/6_Analisis_Historico.py — Análisis histórico multianual
import streamlit as st
from components.mapa_parcelas import render_mapa_parcelas
from components.sidebar_filtros import render_filtros_parcelas
from utils.queries import cargar_parcelas

st.markdown("## 📊 Análisis Histórico")
st.markdown(
    "Exploración multianual de parcelas agrícolas en el Valle de Comayagua. "
    "Visualiza la evolución de cultivos y rendimientos a lo largo de ciclos pasados."
)
st.divider()

with st.sidebar:
    filtros = render_filtros_parcelas()
    st.divider()
    if st.button("🔄 Limpiar caché", use_container_width=True, help="Recarga los datos desde la base de datos"):
        cargar_parcelas.clear()
        st.rerun()

col_mapa, col_detalle = st.columns([3, 1], gap="medium")

with col_mapa:
    resultado = render_mapa_parcelas(filtros)

with col_detalle:
    st.markdown("#### Detalle histórico")

    clicked = (resultado or {}).get("last_object_clicked")
    if clicked and clicked.get("properties"):
        props = clicked["properties"]
        for k, v in props.items():
            if k.startswith("_"):
                continue
            st.markdown(
                f"""
                <div style='display:flex; justify-content:space-between;
                            padding:.25rem 0; border-bottom:1px solid #2d3139;
                            font-size:.9rem;'>
                    <span style='color:#95a5a6;'>{k}</span>
                    <span style='font-weight:600;'>{v}</span>
                </div>
                """,
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
        st.info(
            "Haz clic sobre una parcela en el mapa para ver su información.",
            icon="👆",
        )

    st.markdown("---")
    st.markdown("##### Leyenda")

    modo = filtros.get("modo_color", "cultivo")
    if modo == "cultivo":
        from config import COLORES_CULTIVO
        for cultivo, color in COLORES_CULTIVO.items():
            etiqueta = cultivo.replace("_", " ").title()
            st.markdown(
                f"""
                <div style='display:flex; align-items:center; gap:.6rem;
                            padding:.2rem 0;'>
                    <div style='width:14px; height:14px; border-radius:3px;
                                background:{color}; flex-shrink:0;'></div>
                    <span style='font-size:.85rem;'>{etiqueta}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            """
            <div style='display:flex; justify-content:space-between;
                        font-size:.8rem; color:#95a5a6; margin-bottom:.3rem;'>
                <span>0 qq/ha</span><span>120 qq/ha</span>
            </div>
            <div style='height:14px; border-radius:4px;
                        background:linear-gradient(to right, #2c3e50, #2ecc71);'>
            </div>
            """,
            unsafe_allow_html=True,
        )
