import shutil
import sqlite3
from pathlib import Path
from contextlib import closing
from typing import Literal
import geopandas as gpd
import pandas as pd
from config import GPKG_PRUEBAS_PATH, ROOT
from utils.conexionDB import get_connection_raw, get_db_path


def actualizar_gpkg(
    data,
    mode: str,
    gpkg_path: str | Path | None = None,
    layer_name: str = "parcelas_vigentes",
    crs: str = "EPSG:32616",
    source_layer: str | None = None,
) -> None:
    ruta = Path(gpkg_path) if gpkg_path is not None else get_db_path()

    if ruta.exists() and ruta.stat().st_size == 0:
        ruta.unlink()
        print(f"Archivo corrupto eliminado: {ruta}")

    if isinstance(data, gpd.GeoDataFrame):
        gdf = data.copy()

    elif isinstance(data, (str, Path)):
        src = Path(data)
        if src.suffix.lower() == ".gpkg":
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
            import json
            gdf_origen = gpd.read_file(str(src), layer=source_layer)
            gdf = gpd.read_file(gdf_origen.to_json(), driver="GeoJSON")
            print(f"📦  GeoPackage origen '{src.name}' (capa='{source_layer}'): {len(gdf)} geometrías.")
        else:
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

    modo_pyogrio = "w" if mode == "replace" else "a"

    gdf.to_file(
        ruta,
        layer=layer_name,
        driver="GPKG",
        mode=modo_pyogrio,
    )
    print(f"{len(gdf)} geometrías escritas en '{ruta}' (capa='{layer_name}', modo={mode})")


def _crear_tablas_sql(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS series_diarias_vpm (
            id_parcela                  INTEGER NOT NULL,
            fecha                       DATE    NOT NULL,
            evi_crudo                   REAL,
            lswi_crudo                  REAL,
            temperatura_diaria_promedio REAL,
            radiacion_total_promedio    REAL,
            gpp_diario                  REAL,
            consultado                  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (id_parcela, fecha),
            FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lswi_maximo (
            id_parcela        INTEGER NOT NULL,
            lswi_max          REAL,
            temporada         TEXT,
            PRIMARY KEY (id_parcela, temporada),
            FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS produccion_acumulada_ciclo (
            id_ciclo          INTEGER PRIMARY KEY AUTOINCREMENT,
            id_parcela        INTEGER NOT NULL,
            temporada         TEXT NOT NULL,
            lswi_max          REAL,
            lswi_max_efectivo REAL,
            fecha_inicio      DATE,
            sos               DATE,
            t1                DATE,
            t2                DATE,
            t3                DATE,
            eos               DATE,
            fecha_fin         DATE,
            rendimiento       REAL,
            produccion_total  REAL,
            clasificacion_final TEXT,
            estado_ciclo      TEXT NOT NULL DEFAULT 'candidato',
            CHECK (estado_ciclo IN ('candidato', 'activo', 'finalizado')),
            CHECK (temporada IN ('primera', 'postrera')),
            FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
        );
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ciclo_unico_no_finalizado
            ON produccion_acumulada_ciclo (id_parcela, temporada)
            WHERE estado_ciclo IN ('candidato', 'activo');
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
            rendimiento_estimado_qq_ha      REAL,
            rendimiento_estimado_qq_parcela REAL,
            score_pearson                   REAL,
            score_magnitud_pendiente        REAL,
            score_compuesto                 REAL,
            cultivo_predicho                TEXT,
            fecha_congelamiento        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id_prediccion AUTOINCREMENT),
            UNIQUE (id_ciclo, ventana),
            CHECK (ventana IN ('T1', 'T2', 'T3', 'EOS')),
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patron_referencia_fenologico (
            id_patron         INTEGER NOT NULL,
            subtipo           TEXT    NOT NULL,
            dia_post_sos      INTEGER NOT NULL,
            evi_promedio      REAL    NOT NULL,
            evi_desviacion    REAL,
            mediana_pendiente_verdeo REAL,
            n_muestras        INTEGER NOT NULL,
            ids_parcelas_usadas TEXT  NOT NULL, 
            fecha_construccion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            version           INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (id_patron AUTOINCREMENT),
            UNIQUE (subtipo, dia_post_sos, version)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cobertura_sentinel2 (
            id_cobertura INTEGER NOT NULL,
            fecha        DATE    NOT NULL,
            PRIMARY KEY (id_cobertura AUTOINCREMENT)
        );
    """)

def seeding(rutaGJSON: str) -> None:
    actualizar_gpkg(rutaGJSON, "replace")
    with closing(get_connection_raw()) as conn:
        with conn:
            _crear_tablas_sql(conn)
    print("Seeding completado.")


def validar_gpkg(draft_path: str | Path) -> Path:
    """Copia un GPKG borrador (de delineate_anything/data/delineated/)
    a ROOT/data/ y ejecuta seeding (crea capa parcelas_vigentes + tablas SQL).

    El GPKG destino obtiene el mismo nombre que el borrador.

    Retorna la ruta al GPKG validado.
    """
    draft = Path(draft_path)
    if not draft.exists():
        raise FileNotFoundError(f"Borrador no encontrado: {draft}")
    destino = ROOT / "data" / draft.name

    actualizar_gpkg(
        draft, "replace", gpkg_path=destino,
        source_layer="parcelas_vigentes", layer_name="parcelas_vigentes",
    )

    conn = sqlite3.connect(str(destino))
    try:
        with conn:
            _crear_tablas_sql(conn)
    finally:
        conn.close()

    print(f"✅ GPKG validado: {destino}")
    return destino


def crear_bd_pruebas(geojson_path: str) -> None:
    r = Path(geojson_path)
    if not r.exists():
        print(f"  ❌  Archivo no encontrado: {r.resolve()}")
        return
    geojson = str(r.resolve())

    if GPKG_PRUEBAS_PATH.exists():
        GPKG_PRUEBAS_PATH.unlink()

    from utils.conexionDB import set_db_path

    set_db_path(GPKG_PRUEBAS_PATH)
    seeding(geojson)
    print(f"\n  ✅  BD de pruebas creada: {GPKG_PRUEBAS_PATH}")
    print("  📁  Modo cambiado a: PRUEBAS")
