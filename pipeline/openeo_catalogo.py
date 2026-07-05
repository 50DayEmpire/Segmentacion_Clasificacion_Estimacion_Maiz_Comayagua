# ============================================================
# CELDA: utils/openeo_catalogo.py
# Consulta pura al catálogo openEO/STAC. No conoce la BD.
# ============================================================

import pandas as pd

def obtener_fechas_disponibles_s2(connection, geojson, fecha_inicio, fecha_fin,
                                   collection_id="SENTINEL2_L2A"):
    """
    Consulta el catálogo (vía openEO/STAC) las fechas de adquisición
    de Sentinel-2 L2A disponibles en una extensión espacial y temporal dada.

    connection: conexión openEO ya autenticada
    bbox: dict con west/east/south/north (o geojson de la parcela/zona)
    fecha_inicio, fecha_fin: str "YYYY-MM-DD"
    collection_id: por defecto SENTINEL2_L2A en CDSE

    Retorna: lista ordenada de objetos date (sin duplicados)
    """
    items = connection.list_collection_items(
        collection_id,
        spatial_extent=geojson,
        temporal_extent=[fecha_inicio, fecha_fin],
    )

    fechas = set()
    for item in items:
        propiedades = item.get("properties", item) if isinstance(item, dict) else item.properties
        dt = propiedades.get("datetime") or propiedades.get("start_datetime")
        if dt:
            fechas.add(pd.to_datetime(dt).date())

    return sorted(fechas)


def detectar_fechas_nuevas(fechas_catalogo, fechas_existentes):
    """
    Comparación pura de conjuntos. No hace I/O.

    fechas_catalogo: lista de dates devuelta por obtener_fechas_disponibles_s2
    fechas_existentes: lista/set de dates ya presentes en series_diarias_vpm
                        para la parcela o zona correspondiente

    Retorna: lista ordenada de fechas nuevas (catálogo - existentes)
    """
    return sorted(set(fechas_catalogo) - set(fechas_existentes))

# ============================================================
# CELDA: pipeline/actualizacion_incremental.py
# Aquí sí se toca la BD. Orquesta las dos funciones puras de arriba.
# ============================================================

import sqlite3

def verificar_nuevas_adquisiciones(connection, gpkg_path, id_parcela, bbox,
                                    fecha_inicio, fecha_fin):
    """
    Orquestación con efectos secundarios: lee la BD, consulta el catálogo,
    devuelve solo las fechas nuevas para esa parcela.
    """
    con_bd = sqlite3.connect(gpkg_path)
    con_bd.execute("PRAGMA foreign_keys = ON")

    query = """
        SELECT DISTINCT fecha FROM series_diarias_vpm
        WHERE id_ciclo IN (
            SELECT id_ciclo FROM ciclos WHERE id_parcela = ?
        )
    """
    fechas_existentes = pd.read_sql(query, con_bd, params=(id_parcela,))["fecha"]
    fechas_existentes = set(pd.to_datetime(fechas_existentes).dt.date)
    con_bd.close()

    fechas_catalogo = obtener_fechas_disponibles_s2(
        connection, bbox, fecha_inicio, fecha_fin
    )

    return detectar_fechas_nuevas(fechas_catalogo, fechas_existentes)