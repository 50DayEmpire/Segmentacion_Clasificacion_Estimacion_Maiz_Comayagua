import sqlite3
import streamlit as st
from config import GPKG_PATH


def get_connection_raw() -> sqlite3.Connection:
    """
    Conexión SQLite directa sin caché de Streamlit.
    Usar en scripts de terminal (seeding, migraciones, etc.)
    """
    return sqlite3.connect(GPKG_PATH, check_same_thread=False)


# @st.cache_resource
def get_connection() -> sqlite3.Connection:
    """
    Conexión SQLite con caché de Streamlit.
    Usar exclusivamente desde páginas y componentes del observatorio.
    """
    return sqlite3.connect(GPKG_PATH, check_same_thread=False)
