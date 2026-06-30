# components/estilos.py — CSS global compartido por todas las páginas
"""
Llama a `inyectar_estilos()` al inicio de cada página, después de
st.set_page_config(), para aplicar el tema visual consistente del observatorio.
Sin manipulación de JS ni de selectores internos de Streamlit.
"""
import streamlit as st


def inyectar_estilos() -> None:
    """Inyecta únicamente CSS estándar; sin hacks de JS ni del DOM interno."""
    st.markdown(
        """
        <style>
            /* Espaciado del área de contenido principal.
               El header toolbar de Streamlit mide ~3.75rem; con padding-top
               menor que eso el contenido queda tapado por la barra flotante. */
            .block-container {
                padding-top: 4rem;
                padding-bottom: 1rem;
            }

            /* Fondo del sidebar */
            section[data-testid="stSidebar"] {
                background: #12151a;
                border-right: 1px solid #2d3139;
            }

            /* Tarjetas de métricas */
            div[data-testid="metric-container"] {
                background: #1a1d23;
                border: 1px solid #2d3139;
                border-radius: 8px;
                padding: 0.75rem 1rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
