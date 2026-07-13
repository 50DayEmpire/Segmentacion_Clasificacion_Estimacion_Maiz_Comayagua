# app.py — Entrypoint del observatorio con st.navigation (método recomendado)
#
# Con st.navigation, este archivo actúa como "marco común" de todas las páginas:
# - define la navegación explícitamente
# - inyecta los estilos globales una sola vez
# - el sidebar siempre tiene contenido (el menú de navegación), por lo que
#   el botón de colapso/expansión está siempre disponible.
import streamlit as st
from components.estilos import inyectar_estilos

st.set_page_config(
    page_title="Observatorio Maíz — Valle de Comayagua",
    page_icon="🌽",
    layout="wide",
    initial_sidebar_state="expanded",
)

inyectar_estilos()

# ── Navegación con st.Page + st.navigation ────────────────────────────────────
# st.navigation renderiza el menú en el sidebar y retorna la página activa.
# Al llamar pg.run() se ejecuta el código de esa página dentro de este mismo
# proceso, heredando el set_page_config y los estilos ya aplicados.

pg = st.navigation(
    {
        "Observatorio Regional": [
            st.Page("pages/1_Parcelas.py",         title="Observatorio",      icon="🗺️", default=True),
            st.Page("pages/2_Series_Temporales.py", title="Series Temporales", icon="📈"),
            st.Page("pages/3_Estimacion.py",        title="Estimación",        icon="⚖️"),
            st.Page("pages/4_Resumen_Valle.py",     title="Resumen Valle",     icon="📊"),
        ],
        "Análisis Histórico": [
            st.Page("pages/6_Analisis_Historico.py", title="Análisis Histórico", icon="📅"),
        ],
        "Administración": [
            st.Page("pages/7_Clasificacion_Parcelas.py", title="Clasificación de Parcelas", icon="🏷️"),
            st.Page("pages/8_Segmentacion_Parcelas.py", title="Parcelas", icon="🌱"),
        ],
        "Acerca de": [
            st.Page("pages/5_Acerca_de.py", title="Acerca de", icon="ℹ️"),
        ],
    }
)

pg.run()
