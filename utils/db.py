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
                    evi                         REAL,
                    lswi                        REAL,
                    temperatura_diaria_promedio REAL,
                    gpp_diario                  REAL,
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


def guardar_indices_crudos(
    dfs: dict[str, pd.DataFrame],
    mode: Literal["replace", "append"] = "append",
) -> int:
    """
    Persiste el resultado de ``obtener_datacube_indices_crudo`` en la tabla
    ``series_diarias_vpm`` del GeoPackage SQLite.

    El dict de entrada tiene la forma::

        {
            "EVI":  pd.DataFrame,   # DatetimeIndex × columnas Parcela_N
            "LSWI": pd.DataFrame,   # DatetimeIndex × columnas Parcela_N
        }

    La función hace un FULL OUTER JOIN por (fecha, parcela) entre ambos
    DataFrames para garantizar que se guarden todas las fechas aunque una
    banda no tenga valor en alguna fecha puntual (NaN → NULL en SQLite).

    Parámetros
    ----------
    dfs : dict[str, pd.DataFrame]
        Resultado directo de ``obtener_datacube_indices_crudo``.
        Se esperan las claves ``"EVI"`` y ``"LSWI"``.
    mode : {"append", "replace"}
        - ``"append"`` (defecto): inserta filas nuevas; las que ya existan
          se actualizan con ``INSERT OR REPLACE`` para no duplicar.
        - ``"replace"``: borra **todas** las filas de la tabla antes de insertar.
          Útil para re-ingestas completas de un ciclo.

    Retorna
    -------
    int
        Número de filas escritas en la base de datos.

    Raises
    ------
    KeyError
        Si el dict no contiene alguna de las claves ``"EVI"`` o ``"LSWI"``.
    ValueError
        Si los DataFrames están vacíos o no tienen índice de fechas.
    """
    if "EVI" not in dfs or "LSWI" not in dfs:
        raise KeyError(f"El dict debe contener 'EVI' y 'LSWI'. Claves recibidas: {list(dfs.keys())}")

    df_evi  = dfs["EVI"].copy()
    df_lswi = dfs["LSWI"].copy()

    if df_evi.empty and df_lswi.empty:
        raise ValueError("Ambos DataFrames están vacíos; no hay datos que persistir.")

    # ── Normalizar índice a fecha (sin hora) ──────────────────────────────────
    for df in (df_evi, df_lswi):
        df.index = pd.to_datetime(df.index).normalize()
        df.index.name = "fecha"

    # ── Llevar a formato largo (tidy): una fila por (fecha, parcela) ──────────
    def _a_largo(df: pd.DataFrame, col_valor: str) -> pd.DataFrame:
        largo = (
            df.reset_index()
              .melt(id_vars="fecha", var_name="parcela_col", value_name=col_valor)
        )
        # Extraer id_parcela del nombre de columna.
        # Soporta el formato seguro "id_<N>" (ej. "id_42") generado por ingesta,
        # y como fallback el formato posicional legacy "Parcela_<N>" (ej. "Parcela_1").
        id_extraido = largo["parcela_col"].str.extract(r"^id_(\d+)$")[0]
        if id_extraido.isna().all():
            # fallback: "Parcela_N" — posicional, menos confiable
            id_extraido = largo["parcela_col"].str.extract(r"(\d+)$")[0]
        largo["id_parcela"] = id_extraido.astype(int)
        return largo[["id_parcela", "fecha", col_valor]]

    largo_evi  = _a_largo(df_evi,  "evi_crudo")
    largo_lswi = _a_largo(df_lswi, "lswi_crudo")

    # ── Unir EVI y LSWI por (id_parcela, fecha) ───────────────────────────────
    merged = pd.merge(
        largo_evi,
        largo_lswi,
        on=["id_parcela", "fecha"],
        how="outer",
    )

    # Columnas opcionales que la tabla acepta como NULL si no vienen aún
    for col in ("evi_suavizado", "lswi_suavizado", "temperatura_diaria_promedio", "gpp_diario"):
        if col not in merged.columns:
            merged[col] = None

    merged["fecha"] = merged["fecha"].dt.strftime("%Y-%m-%d")

    rows = list(
        merged[["id_parcela", "fecha", "evi_crudo", "lswi_crudo"]].itertuples(
            index=False, name=None
        )
    )

    if not rows:
        print("⚠️  Sin filas para escribir.")
        return 0

    sql_insert = """
        INSERT OR REPLACE INTO series_diarias_vpm
            (id_parcela, fecha, evi_crudo, lswi_crudo)
        VALUES (?, ?, ?, ?)
    """

    with closing(get_connection_raw()) as conn:
        with conn:
            if mode == "replace":
                conn.execute("DELETE FROM series_diarias_vpm;")
                print("🗑️  Tabla series_diarias_vpm vaciada (mode='replace').")
            conn.executemany(sql_insert, rows)

    n = len(rows)
    print(f"✅  {n} filas escritas en 'series_diarias_vpm' (mode='{mode}').")
