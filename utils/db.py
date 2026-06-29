from pathlib import Path
import geopandas as gpd
from config import GPKG_PATH
from conexionDB import get_connection

def actualizar_gpkg(
    data,
    mode: str,
    gpkg_path: str = GPKG_PATH,
    layer_name: str = "parcelas_vigentes",
    crs: str = "EPSG:32616"
):
    """
    Actualiza un GeoPackage con geometrías, desde un archivo o un GeoDataFrame.

    Parámetros:
    -----------
    data : str | gpd.GeoDataFrame
        Ruta a archivo vectorial (GeoJSON, Shapefile, etc.) o un GeoDataFrame en memoria.
    gpkg_path : str
        Ruta al GeoPackage destino.
    layer_name : str
        Nombre de la capa dentro del GeoPackage.
    mode : str
        Modo de escritura: "replace" (sobrescribe) o "append" (agrega).
    crs : str
        CRS métrico para cálculos de área (por defecto EPSG:32616).
    """
    # Cargar datos según tipo
    if isinstance(data, str):
        gdf = gpd.read_file(data)
    elif isinstance(data, gpd.GeoDataFrame):
        gdf = data.copy()
    else:
        raise ValueError("El parámetro 'data' debe ser ruta a archivo o un GeoDataFrame.")

    # Asegurar CRS métrico
    gdf = gdf.to_crs(crs)

    # Calcular área en hectáreas
    gdf["area_ha"] = gdf.geometry.area / 10_000
    gdf["area_m2"] = gdf.geometry.area

    # Normalizar columnas mínimas necesarias
    gdf = gdf[["geometry", "area_m2", "area_ha"]].copy()

    # Manejo de IDs
    if mode == "replace":
        # Reinicia IDs desde cero
        gdf.index.name = "id_parcela"
        gdf = gdf.reset_index()
    elif mode == "append":
        # Leer capa existente para calcular último ID
        try:
            existente = gpd.read_file(gpkg_path, layer=layer_name)
            ultimo_id = existente["id_parcela"].max() if len(existente) > 0 else -1
        except Exception:
            # Si la capa no existe aún, empezar desde -1
            ultimo_id = -1

        # Asignar IDs consecutivos a los nuevos registros
        gdf["id_parcela"] = range(ultimo_id + 1, ultimo_id + 1 + len(gdf))

    # Escribir al GeoPackage
    gdf.to_file(
        Path(gpkg_path),
        layer=layer_name,
        driver="GPKG",
        if_exists=mode
    )
    print(f"{len(gdf)} geometrías escritas en {gpkg_path} (modo={mode})")

def seeding(rutaGJSON):
    conn = get_connection()
    cur = conn.cursor()

    actualizar_gpkg(rutaGJSON, "replace")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS series_diarias_vpm (
        id_parcela INTEGER NOT NULL,
        fecha DATE NOT NULL,
        evi_crudo REAL NOT NULL,
        evi_suavizado REAL,
        lswi_crudo REAL NOT NULL,
        lswi_suavizado REAL,
        temperatura_diaria_promedio REAL NOT NULL,
        gpp_diario REAL NOT NULL,
        FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS produccion_acumulada_ciclo (
        id_parcela INTEGER NOT NULL,
        fecha_inicio DATE NOT NULL,
        fecha_fin DATE NOT NULL,
        rendimiento REAL NOT NULL,
        produccion_total REAL NOT NULL,
        FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
    );
    """)