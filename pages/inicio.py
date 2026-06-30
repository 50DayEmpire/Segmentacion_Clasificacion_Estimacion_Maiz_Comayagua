# pages/inicio.py — Página de bienvenida del observatorio
import streamlit as st

# ── Cabecera ───────────────────────────────────────────────────────────────────
col_logo, col_titulo = st.columns([1, 8])
with col_logo:
    st.markdown("# 🌽")
with col_titulo:
    st.markdown(
        """
        <h1 style='margin:0; padding:0; font-size:1.8rem;'>
            Observatorio de Producción de Maíz
        </h1>
        <p style='margin:0; color:#95a5a6; font-size:0.95rem;'>
            Valle de Comayagua, Honduras — Estimación temprana de rendimiento
            mediante imágenes Sentinel-2
        </p>
        """,
        unsafe_allow_html=True,
    )

st.divider()

# ── Cuerpo ─────────────────────────────────────────────────────────────────────
col_izq, col_der = st.columns([3, 2], gap="large")

with col_izq:
    st.markdown("### Acerca del sistema")
    st.markdown(
        """
        Este observatorio es la capa de visualización del pipeline de estimación
        temprana de rendimiento de maíz desarrollado como parte de la tesis:

        > *"Análisis prospectivo de la producción agrícola en Honduras mediante el
        > procesamiento de datos multiespectrales satelitales con inteligencia artificial"*

        El pipeline integra tres etapas:

        - **Segmentación** de parcelas agrícolas con SAMGeo
        - **Clasificación** de cultivos con Random Forest sobre índices espectrales tempranos
        - **Estimación** de rendimiento mediante el modelo VPM *(Vegetation Photosynthesis Model)*

        Los resultados se expresan en **quintales por hectárea (qq/ha)** y
        **quintales por parcela (qq/parcela)** para los ciclos *primera* y *postrera*.
        """
    )
    st.info("📌 Selecciona una vista en el menú lateral para explorar el observatorio.")

with col_der:
    st.markdown("### Ciclos de siembra")

    col_p, col_q = st.columns(2)
    with col_p:
        st.markdown(
            """
            <div style='background:#1a1d23; border-left:4px solid #2ecc71;
                        border-radius:6px; padding:0.8rem 1rem;'>
                <div style='color:#2ecc71; font-weight:700; font-size:0.85rem;
                            text-transform:uppercase; letter-spacing:.05em;'>
                    Primera
                </div>
                <div style='font-size:1.1rem; margin-top:.25rem;'>Mayo — Octubre</div>
                <div style='color:#95a5a6; font-size:0.8rem; margin-top:.25rem;'>
                    Ciclo principal
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_q:
        st.markdown(
            """
            <div style='background:#1a1d23; border-left:4px solid #e67e22;
                        border-radius:6px; padding:0.8rem 1rem;'>
                <div style='color:#e67e22; font-weight:700; font-size:0.85rem;
                            text-transform:uppercase; letter-spacing:.05em;'>
                    Postrera
                </div>
                <div style='font-size:1.1rem; margin-top:.25rem;'>Agosto — Enero</div>
                <div style='color:#95a5a6; font-size:0.8rem; margin-top:.25rem;'>
                    Ciclo secundario
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("### Ventanas de predicción")
    for ventana, descripcion in {
        "T1": "Predicción temprana — inicio de ciclo",
        "T2": "Predicción media — floración",
        "T3": "Predicción tardía — llenado de grano",
    }.items():
        st.markdown(
            f"""
            <div style='display:flex; align-items:center; gap:.75rem;
                        padding:.4rem 0; border-bottom:1px solid #2d3139;'>
                <span style='background:#2ecc71; color:#000; font-weight:700;
                             border-radius:4px; padding:.1rem .5rem;
                             font-size:.85rem;'>{ventana}</span>
                <span style='color:#bdc3c7; font-size:.9rem;'>{descripcion}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.divider()

# ── Tarjetas de navegación ─────────────────────────────────────────────────────
st.markdown("### Vistas disponibles")
c1, c2, c3, c4 = st.columns(4, gap="medium")

_card = (
    "background:#1a1d23; border:1px solid #2d3139; border-radius:10px; "
    "padding:1.1rem 1rem; height:160px;"
)

with c1:
    st.markdown(
        f"<div style='{_card} border-top:3px solid #2ecc71;'>"
        "<div style='font-size:1.6rem;'>🗺️</div>"
        "<div style='font-weight:700; margin:.4rem 0 .25rem;'>Parcelas</div>"
        "<div style='color:#95a5a6; font-size:.85rem;'>Mapa interactivo coloreado "
        "por cultivo o rendimiento estimado.</div></div>",
        unsafe_allow_html=True,
    )
with c2:
    st.markdown(
        f"<div style='{_card} border-top:3px solid #3498db;'>"
        "<div style='font-size:1.6rem;'>📈</div>"
        "<div style='font-weight:700; margin:.4rem 0 .25rem;'>Series Temporales</div>"
        "<div style='color:#95a5a6; font-size:.85rem;'>Curvas EVI, LSWI y GPP "
        "con marcadores SOS y POS.</div></div>",
        unsafe_allow_html=True,
    )
with c3:
    st.markdown(
        f"<div style='{_card} border-top:3px solid #e67e22;'>"
        "<div style='font-size:1.6rem;'>⚖️</div>"
        "<div style='font-weight:700; margin:.4rem 0 .25rem;'>Estimación</div>"
        "<div style='color:#95a5a6; font-size:.85rem;'>qq/ha estimado vs referencia "
        "SAG/CAN por ventana T1/T2/T3.</div></div>",
        unsafe_allow_html=True,
    )
with c4:
    st.markdown(
        f"<div style='{_card} border-top:3px solid #9b59b6;'>"
        "<div style='font-size:1.6rem;'>📊</div>"
        "<div style='font-weight:700; margin:.4rem 0 .25rem;'>Resumen Valle</div>"
        "<div style='color:#95a5a6; font-size:.85rem;'>Producción total agregada "
        "en quintales y métricas de validación.</div></div>",
        unsafe_allow_html=True,
    )

st.markdown(
    """
    <hr style='border-color:#2d3139; margin-top:2rem;'/>
    <div style='text-align:center; color:#4a5568; font-size:.8rem; padding:.5rem 0;'>
        Sistema de estimación temprana de rendimiento — Valle de Comayagua, Honduras •
        Datos: ESA Sentinel-2 (Copernicus) • Referencia: SAG / CAN
    </div>
    """,
    unsafe_allow_html=True,
)
