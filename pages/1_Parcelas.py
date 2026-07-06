# pages/1_Parcelas.py — Vista de mapa interactivo de parcelas
import streamlit as st
from components.mapa_parcelas import render_mapa_parcelas
from components.sidebar_filtros import render_filtros_parcelas
from utils.queries import cargar_parcelas

# ── Título ─────────────────────────────────────────────────────────────────────
st.markdown("## 🗺️ Parcelas")
st.markdown(
    "Mapa interactivo de parcelas agrícolas segmentadas en el Valle de Comayagua. "
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

# ── Layout principal: mapa + panel lateral de detalle ─────────────────────────
col_mapa, col_detalle = st.columns([3, 1], gap="medium")

with col_mapa:
    resultado = render_mapa_parcelas(filtros)

with col_detalle:
    st.markdown("#### Detalle de parcela")

    clicked = (resultado or {}).get("last_object_clicked")
    if clicked and isinstance(clicked, dict) and "id_parcela" in clicked:
        props = clicked
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
                    area_ha = fila.iloc[0].get("area_ha", None)
                    area_m2 = fila.iloc[0].get("area_m2", None)
                    if area_ha is not None:
                        st.metric("Área (ha)", f"{area_ha:.4f}")
                    if area_m2 is not None:
                        st.metric("Área (m²)", f"{area_m2:.2f}")
            except Exception:
                pass
        if "id_parcela" in props:
            pid = int(props["id_parcela"])
            st.markdown("---")
            if st.button(f"📈 Ver Series Temporales — parcela {pid}", use_container_width=True):
                st.session_state["parcela_series"] = pid
                st.switch_page("pages/2_Series_Temporales.py")
    else:
        st.info(
            "Haz clic sobre una parcela en el mapa para ver su información.",
            icon="👆",
        )

    st.markdown("---")
