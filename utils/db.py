from pathlib import Path
from contextlib import closing
import geopandas as gpd
from config import GPKG_PATH
from utils.conexionDB import get_connection_raw


def actualizar_gpkg(
    data,
    mode: str,
    gpkg_path: str = GPKG_PATH,
    layer_name: str = "parcelas_vigentes",
    crs: str = "EPSG:32616",
) -> None:
    """
    Actualiza un GeoPackage con geometrías, desde un archivo o un GeoDataFrame.

    Parámetros
    ----------
    data : str | gpd.GeoDataFrame
        Ruta a archivo vectorial (GeoJSON, Shapefile, etc.) o un GeoDataFrame.
    gpkg_path : str
        Ruta al GeoPackage destino.
    layer_name : str
        Nombre de la capa dentro del GeoPackage.
    mode : str
        Modo de escritura: "replace" (sobrescribe) o "append" (agrega).
    crs : str
        CRS métrico para cálculos de área (por defecto EPSG:32616).
    """
    ruta = Path(gpkg_path)

    # Si el archivo existe pero está vacío o corrupto (0 bytes), eliminarlo
    # para que pyogrio pueda crear uno nuevo limpio.
    if ruta.exists() and ruta.stat().st_size == 0:
        ruta.unlink()
        print(f"Archivo corrupto eliminado: {ruta}")

    if isinstance(data, str):
        gdf = gpd.read_file(data)
    elif isinstance(data, gpd.GeoDataFrame):
        gdf = data.copy()
    else:
        raise ValueError("'data' debe ser ruta a archivo o un GeoDataFrame.")

    gdf = gdf.to_crs(crs)
    gdf["area_ha"] = gdf.geometry.area / 10_000
    gdf["area_m2"] = gdf.geometry.area
    gdf = gdf[["geometry", "area_m2", "area_ha"]].copy()

    if mode == "replace":
        gdf.index.name = "id_parcela"
        gdf = gdf.reset_index()
    elif mode == "append":
        try:
            existente = gpd.read_file(str(ruta), layer=layer_name)
            ultimo_id = existente["id_parcela"].max() if len(existente) > 0 else -1
        except Exception:
            ultimo_id = -1
        gdf["id_parcela"] = range(ultimo_id + 1, ultimo_id + 1 + len(gdf))

    # geopandas >= 1.0 con pyogrio usa mode="w"/"a", no if_exists
    modo_pyogrio = "w" if mode == "replace" else "a"

    gdf.to_file(
        ruta,
        layer=layer_name,
        driver="GPKG",
        mode=modo_pyogrio,
    )
    print(f"{len(gdf)} geometrías escritas en '{ruta}' (capa='{layer_name}', modo={mode})")


def seeding(rutaGJSON: str) -> None:
    """
    Inicializa el GeoPackage: carga las parcelas y crea las tablas auxiliares.

    Orden obligatorio en Windows:
    1. Escribir geometrías con geopandas/pyogrio (handle GDAL).
    2. Abrir SQLite DESPUÉS de que GDAL liberó el archivo.
    GDAL y SQLite no pueden tener el archivo abierto simultáneamente para escritura.

    WAL mode se activa aquí para que las escrituras futuras del pipeline
    no bloqueen las lecturas de Streamlit.
    """
    # 1. Escribir geometrías — GDAL abre y cierra el archivo aquí
    actualizar_gpkg(rutaGJSON, "replace")

    # 2. Abrir SQLite solo después de que GDAL liberó el archivo.
    with closing(get_connection_raw()) as conn:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS series_diarias_vpm (
                    id_parcela                  INTEGER NOT NULL,
                    fecha                       DATE    NOT NULL,
                    evi_crudo                   REAL    NOT NULL,
                    evi_suavizado               REAL,
                    lswi_crudo                  REAL    NOT NULL,
                    lswi_suavizado              REAL,
                    temperatura_diaria_promedio REAL    NOT NULL,
                    gpp_diario                  REAL    NOT NULL,
                    PRIMARY KEY (id_parcela, fecha),
                    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS produccion_acumulada_ciclo (
                    id_parcela       INTEGER NOT NULL,
                    fecha_inicio     DATE    NOT NULL,
                    fecha_fin        DATE    NOT NULL,
                    rendimiento      REAL    NOT NULL,
                    produccion_total REAL    NOT NULL,
                    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
                );
            """)

    print("Seeding completado.")
