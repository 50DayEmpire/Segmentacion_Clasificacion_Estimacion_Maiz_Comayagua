# tests/conftest.py
import sqlite3
import pytest
from pathlib import Path
from utils.db import actualizar_gpkg

@pytest.fixture
def conn_prueba(tmp_path, monkeypatch):
    """
    Crea una BD SQLite temporal con el esquema completo y una capa mínima
    de parcelas, y parchea get_connection_raw para que todas las funciones del pipeline
    escriban ahí durante el test, no en el GeoPackage real.
    """
    db_path = tmp_path / "test.gpkg"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    # ejecutar aquí los mismos CREATE TABLE del esquema real
    conn.executescript(Path("tests/fixtures/esquema_test.sql").read_text())

    # --- Simular capa de parcelas (como hace seeding con geopandas) ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parcelas_vigentes (
            id_parcela INTEGER PRIMARY KEY,
            area_ha REAL
        );
    """)

    def _get_connection_raw():
        return sqlite3.connect(str(db_path))

    monkeypatch.setattr("utils.conexionDB.get_connection_raw", _get_connection_raw)

    yield conn
    conn.close()