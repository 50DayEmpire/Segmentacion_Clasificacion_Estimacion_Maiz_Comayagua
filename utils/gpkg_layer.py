import sqlite3
import logging
import io
from pathlib import Path
from contextlib import closing

import fiona
import geopandas as gpd

logger = logging.getLogger(__name__)

CAPA_DESTINO = "parcelas_vigentes"
CAPA_ORIGEN = "fields"


def _eliminar_capa(path: Path, layer: str) -> None:
    """Borra completamente una capa del GPKG. SQLite cascadea el rtree."""
    with closing(sqlite3.connect(str(path))) as conn:
        with conn:
            conn.execute(f'DROP TABLE IF EXISTS "{layer}"')
            conn.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (layer,))
            conn.execute("DELETE FROM gpkg_geometry_columns WHERE table_name = ?", (layer,))
            conn.execute("DELETE FROM gpkg_ogr_contents WHERE table_name = ?", (layer,))
    logger.debug("Capa '%s' eliminada de %s", layer, path.name)


def _escribir_capa(gdf: gpd.GeoDataFrame, path: Path, layer: str) -> int:
    """Escribe un GeoDataFrame como capa nueva en GPKG."""
    import pyogrio
    pyogrio.write_dataframe(gdf, str(path), layer=layer, driver="GPKG")
    logger.debug("%d features escritos en %s/%s", len(gdf), path.name, layer)
    return len(gdf)


def _deduplicar_capa(path: Path, layer: str) -> int:
    """Elimina geometrías duplicadas de una capa. Retorna features tras dedup."""
    gdf = gpd.read_file(str(path), layer=layer)
    n_antes = len(gdf)
    gdf = gdf.drop_duplicates(subset="geometry")
    n_despues = len(gdf)
    if n_despues < n_antes:
        n_dup = n_antes - n_despues
        logger.info("Eliminados %d duplicados de %s/%s", n_dup, path.name, layer)
        _eliminar_capa(path, layer)
        _escribir_capa(gdf, path, layer)
    return n_despues


def asegurar_capa_parcelas(gpkg_path: str | Path) -> str:
    """Renombra 'fields' → 'parcelas_vigentes' y limpia duplicados.

    Es idempotente.  Retorna el nombre de capa a usar para leer/escribir.
    """
    path = Path(gpkg_path)
    if not path.exists():
        logger.warning("GPKG no encontrado: %s", path)
        return CAPA_DESTINO

    capas = fiona.listlayers(str(path))
    logger.debug("Capas en %s: %s", path.name, capas)

    if CAPA_DESTINO not in capas and CAPA_ORIGEN in capas:
        try:
            with closing(sqlite3.connect(str(path))) as conn:
                with conn:
                    conn.execute(
                        "UPDATE gpkg_contents SET table_name = ? WHERE table_name = ?",
                        (CAPA_DESTINO, CAPA_ORIGEN),
                    )
                    conn.execute(
                        "UPDATE gpkg_geometry_columns SET table_name = ? WHERE table_name = ?",
                        (CAPA_DESTINO, CAPA_ORIGEN),
                    )
                    conn.execute(f'ALTER TABLE "{CAPA_ORIGEN}" RENAME TO "{CAPA_DESTINO}"')
            logger.info("Capa '%s' renombrada a '%s' en %s", CAPA_ORIGEN, CAPA_DESTINO, path.name)
        except Exception as e:
            logger.error("Error al renombrar capa en %s: %s", path.name, e)

    if CAPA_DESTINO in capas or CAPA_DESTINO in fiona.listlayers(str(path)):
        _deduplicar_capa(path, CAPA_DESTINO)
        return CAPA_DESTINO

    if capas:
        return capas[0]
    return CAPA_DESTINO


def limpiar_legacy(path: str | Path) -> None:
    """Elimina tabla 'fields' si existe y hace VACUUM para recuperar espacio."""
    path = Path(path)
    if not path.exists():
        return
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("DROP TABLE IF EXISTS \"fields\"")
        conn.execute("DELETE FROM gpkg_contents WHERE table_name = 'fields'")
        conn.execute("DELETE FROM gpkg_geometry_columns WHERE table_name = 'fields'")
        conn.execute("DELETE FROM gpkg_ogr_contents WHERE table_name = 'fields'")
    with closing(sqlite3.connect(str(path))) as conn2:
        conn2.execute("VACUUM")
