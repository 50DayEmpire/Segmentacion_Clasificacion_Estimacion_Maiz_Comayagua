from pathlib import Path
from contextlib import closing
from typing import Literal
import geopandas as gpd
import pandas as pd
from config import GPKG_PATH
from utils.conexionDB import get_connection_raw


def actualizar_gpkg(
    data,
    mode: str,
    gpkg_path: str = GPKG_PATH,
    layer_name: str = "parcelas_vigentes",
    crs: str = "EPSG:32616",
    source_layer: str | None = None,
) -> None:
    """
    Actualiza un GeoPackage con geometrías, desde un archivo o un GeoDataFrame.

    Parámetros
    ----------
    data : str | Path | gpd.GeoDataFrame
        - Ruta a GeoJSON, Shapefile u otro vectorial: se lee directamente.
        - Ruta a GeoPackage (``.gpkg``): se convierte a GeoDataFrame pasando
          por GeoJSON en memoria (normaliza el esquema antes de escribir).
          Usa ``source_layer`` para seleccionar la capa de origen; si se omite
          se lee la primera capa disponible.
        - ``gpd.GeoDataFrame``: se usa tal cual.
    gpkg_path : str
        Ruta al GeoPackage destino.
    layer_name : str
        Nombre de la capa dentro del GeoPackage destino.
    mode : str
        Modo de escritura: ``"replace"`` (sobrescribe) o ``"append"`` (agrega).
    crs : str
        CRS métrico para cálculos de área (por defecto EPSG:32616).
    source_layer : str | None
        Solo relevante cuando ``data`` es un ``.gpkg``.
        Nombre de la capa de origen a leer. Si es ``None`` se usa la primera
        capa listada por fiona/pyogrio.
    """
    ruta = Path(gpkg_path)

    # Si el archivo existe pero está vacío o corrupto (0 bytes), eliminarlo
    # para que pyogrio pueda crear uno nuevo limpio.
    if ruta.exists() and ruta.stat().st_size == 0:
        ruta.unlink()
        print(f"Archivo corrupto eliminado: {ruta}")

    # ── Carga de datos de entrada ─────────────────────────────────────────────
    if isinstance(data, gpd.GeoDataFrame):
        gdf = data.copy()

    elif isinstance(data, (str, Path)):
        src = Path(data)
        if src.suffix.lower() == ".gpkg":
            # Determinar capa de origen
            if source_layer is None:
                import fiona
                capas = fiona.listlayers(str(src))
                if not capas:
                    raise ValueError(f"El GeoPackage '{src}' no contiene ninguna capa.")
                source_layer = capas[0]
                if len(capas) > 1:
                    print(
                        f"⚠️  '{src.name}' tiene {len(capas)} capas. "
                        f"Leyendo '{source_layer}'. "
                        f"Usa source_layer= para elegir otra: {capas}"
                    )
            # Leer → serializar a GeoJSON en memoria → deserializar
            # Esto normaliza el esquema y elimina metadatos propietarios del .gpkg origen.
            import json
            gdf_origen = gpd.read_file(str(src), layer=source_layer)
            gdf = gpd.read_file(gdf_origen.to_json(), driver="GeoJSON")
            print(f"📦  GeoPackage origen '{src.name}' (capa='{source_layer}'): {len(gdf)} geometrías.")
        else:
            # GeoJSON, Shapefile, KML, etc.
            gdf = gpd.read_file(str(src))

    else:
        raise ValueError(
            f"'data' debe ser ruta a archivo (str/Path) o un GeoDataFrame. "
            f"Recibido: {type(data).__name__}"
        )

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
                    evi_crudo                   REAL,
                    lswi_crudo                  REAL,
                    temperatura_diaria_promedio REAL,
                    radiacion_total_promedio    REAL,
                    gpp_diario                  REAL,
                    PRIMARY KEY (id_parcela, fecha),
                    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS produccion_acumulada_ciclo (
                    id_ciclo         INTEGER NOT NULL,
                    id_parcela       INTEGER NOT NULL,
                    temporada        TEXT,
                    lswi_max         REAL,
                    sos              DATE,
                    t1               DATE,
                    t2               DATE,
                    t3               DATE,
                    eos              DATE,
                    fecha_inicio     DATE,
                    fecha_fin        DATE,
                    rendimiento      REAL,
                    produccion_total REAL,
                    estado_ciclo     TEXT,
                    PRIMARY KEY (id_ciclo),
                    CHECK (estado_ciclo IN ('candidato', 'activo', 'finalizado'))
                    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
                    UNIQUE (id_parcela, fecha_inicio, fecha_fin)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indices_suavizados(
                    id_ciclo         INTEGER NOT NULL,
                    fecha            DATE    NOT NULL,
                    id_parcela       INTEGER NOT NULL,
                    evi              REAL,
                    lswi             REAL,
                    PRIMARY KEY (id_ciclo, fecha)
                    FOREIGN KEY (id_ciclo) REFERENCES produccion_acumulada_ciclo(id_ciclo)
                    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS predicciones_ventana (
                    id_prediccion              INTEGER NOT NULL,
                    id_ciclo                   INTEGER NOT NULL,
                    id_parcela                 INTEGER NOT NULL,
                    ventana                    TEXT    NOT NULL,
                    fecha_ventana              DATE    NOT NULL,
                    lswi_max_efectivo_usado    REAL,
                    gpp_acumulado              REAL,
                    npp_acumulado              REAL,
                    rendimiento_estimado_qq_ha       REAL,
                    rendimiento_estimado_qq_parcela  REAL,
                    fecha_congelamiento        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id_prediccion AUTOINCREMENT),
                    UNIQUE (id_ciclo, ventana),
                    CHECK (ventana IN ('T1', 'T2', 'T3')),
                    FOREIGN KEY (id_ciclo) REFERENCES produccion_acumulada_ciclo(id_ciclo),
                    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS climatologia_diaria (
                    id_region           INTEGER NOT NULL DEFAULT 1,
                    variable            TEXT    NOT NULL,
                    dia_anio            INTEGER NOT NULL,
                    valor_climatologico REAL    NOT NULL,
                    anio_min_incluido   INTEGER NOT NULL,
                    anio_max_incluido   INTEGER NOT NULL,
                    fecha_calculo       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id_region, variable, dia_anio),
                    CHECK (variable IN ('PAR', 'temperatura')),
                    CHECK (dia_anio BETWEEN 1 AND 366)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS series_extrapoladas_ventana (
                    id_prediccion    INTEGER NOT NULL,
                    fecha            DATE    NOT NULL,
                    evi_extrapolado  REAL,
                    lswi_extrapolado REAL,
                    PRIMARY KEY (id_prediccion, fecha),
                    FOREIGN KEY (id_prediccion) REFERENCES predicciones_ventana(id_prediccion)
                        ON DELETE CASCADE
                );
            """)

    print("Seeding completado.")
