import sqlite3
from pathlib import Path
import streamlit as st
from config import GPKG_PATH, GPKG_PRUEBAS_PATH, ROOT


_ACTIVE_DB_PATH: Path = GPKG_PATH


def set_db_path(path: str | Path) -> None:
    global _ACTIVE_DB_PATH
    _ACTIVE_DB_PATH = Path(path)


def set_db_mode(mode: str) -> None:
    """Deprecated — usar set_db_path(). Mantenido para compatibilidad con CLI."""
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


def listar_gpkgs_validados() -> list[Path]:
    """Escanea ROOT/data/*.gpkg y retorna solo los que tienen seeding
    (tabla series_diarias_vpm presente)."""
    gpkgs: list[Path] = []
    data_dir = ROOT / "data"
    if not data_dir.is_dir():
        return gpkgs
    for f in sorted(data_dir.glob("*.gpkg")):
        try:
            conn = sqlite3.connect(str(f))
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='series_diarias_vpm'"
            )
            if cursor.fetchone():
                gpkgs.append(f)
            conn.close()
        except Exception:
            continue
    return gpkgs


def get_connection_raw() -> sqlite3.Connection:
    return sqlite3.connect(str(_ACTIVE_DB_PATH), check_same_thread=False)


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(str(_ACTIVE_DB_PATH), check_same_thread=False)
