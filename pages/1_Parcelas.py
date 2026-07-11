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

    st.markdown("---")
    if st.button(f"📈 Ver Series Temporales — parcela {pid}", use_container_width=True):
        st.session_state["parcela_series"] = pid
        st.switch_page("pages/2_Series_Temporales.py")
else:
    st.info(
        "Haz clic sobre una parcela en el mapa para ver su información.",
        icon="👆",
    )
