import sqlite3
from pathlib import Path
import streamlit as st
from config import GPKG_PATH, GPKG_PRUEBAS_PATH


_ACTIVE_DB_PATH: Path = GPKG_PATH


def set_db_mode(mode: str) -> None:
    global _ACTIVE_DB_PATH
    if mode == "real":
        _ACTIVE_DB_PATH = GPKG_PATH
    elif mode == "pruebas":
        _ACTIVE_DB_PATH = GPKG_PRUEBAS_PATH
    else:
        raise ValueError(f"Modo inválido: '{mode}'. Use 'real' o 'pruebas'.")
    print(f"  📁 Modo BD cambiado a: {mode.upper()} ({_ACTIVE_DB_PATH})")


def get_db_path() -> Path:
    return _ACTIVE_DB_PATH


def get_connection_raw() -> sqlite3.Connection:
    """
    Conexión SQLite directa sin caché de Streamlit.
    Usar en scripts de terminal (seeding, migraciones, etc.)
    """
    return sqlite3.connect(str(_ACTIVE_DB_PATH), check_same_thread=False)


# @st.cache_resource
def get_connection() -> sqlite3.Connection:
    """
    Conexión SQLite con caché de Streamlit.
    Usar exclusivamente desde páginas y componentes del observatorio.
    """
    return sqlite3.connect(str(_ACTIVE_DB_PATH), check_same_thread=False)
