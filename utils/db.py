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
                    PRIMARY KEY (id_ciclo),
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

    print("Seeding completado.")


def guardar_indices_crudos(
    dfs: dict[str, pd.DataFrame],
    mode: Literal["replace", "append"] = "append",
) -> int:
    """
    Persiste el resultado de ``obtener_datacube_indices_crudo`` en la tabla
    ``series_diarias_vpm`` del GeoPackage SQLite.

    Solo escribe evi_crudo y lswi_crudo. Nunca toca gpp_diario ni
    temperatura_diaria_promedio, que son responsabilidad de otras etapas
    del pipeline.

    Parámetros
    ----------
    dfs : dict[str, pd.DataFrame]
        Resultado directo de ``obtener_datacube_indices_crudo``.
        Se esperan las claves ``"EVI"`` y ``"LSWI"``.
    mode : {"append", "replace"}
        - ``"append"`` (defecto): upsert por (id_parcela, fecha). Si la fila
          ya existe, evi_crudo/lswi_crudo solo se rellenan si están en NULL
          (inmutabilidad del crudo); gpp_diario y temperatura_diaria_promedio
          nunca se tocan.
        - ``"replace"``: borra las filas existentes de las parcelas y el
          rango de fechas presentes en ``dfs`` (no toda la tabla) antes de
          insertar. Útil para re-ingestas completas de un ciclo específico.

    Retorna
    -------
    int
        Número de filas escritas en la base de datos.

    Raises
    ------
    KeyError
        Si el dict no contiene alguna de las claves ``"EVI"`` o ``"LSWI"``.
    ValueError
        Si los DataFrames están vacíos, no tienen índice de fechas, o algún
        nombre de columna no permite extraer id_parcela.
    """
    if "EVI" not in dfs or "LSWI" not in dfs:
        raise KeyError(f"El dict debe contener 'EVI' y 'LSWI'. Claves recibidas: {list(dfs.keys())}")

    df_evi  = dfs["EVI"].copy()
    df_lswi = dfs["LSWI"].copy()

    if df_evi.empty and df_lswi.empty:
        raise ValueError("Ambos DataFrames están vacíos; no hay datos que persistir.")

    for df in (df_evi, df_lswi):
        df.index = pd.to_datetime(df.index).normalize()
        df.index.name = "fecha"

    def _a_largo(df: pd.DataFrame, col_valor: str) -> pd.DataFrame:
        largo = (
            df.reset_index()
              .melt(id_vars="fecha", var_name="parcela_col", value_name=col_valor)
        )
        id_extraido = largo["parcela_col"].str.extract(r"^id_(\d+)$")[0]
        if id_extraido.isna().all():
            id_extraido = largo["parcela_col"].str.extract(r"(\d+)$")[0]

        if id_extraido.isna().any():
            problematicas = largo.loc[id_extraido.isna(), "parcela_col"].unique().tolist()
            raise ValueError(
                f"No se pudo extraer id_parcela de las columnas: {problematicas}"
            )

        largo["id_parcela"] = id_extraido.astype(int)
        return largo[["id_parcela", "fecha", col_valor]]

    largo_evi  = _a_largo(df_evi,  "evi_crudo")
    largo_lswi = _a_largo(df_lswi, "lswi_crudo")

    merged = pd.merge(largo_evi, largo_lswi, on=["id_parcela", "fecha"], how="outer")
    merged["fecha"] = merged["fecha"].dt.strftime("%Y-%m-%d")

    df_rows = merged[["id_parcela", "fecha", "evi_crudo", "lswi_crudo"]].astype(object)
    df_rows = df_rows.where(pd.notna(df_rows), None)
    rows = list(df_rows.itertuples(index=False, name=None))

    if not rows:
        print("⚠️  Sin filas para escribir.")
        return 0

    sql_upsert = """
        INSERT INTO series_diarias_vpm (id_parcela, fecha, evi_crudo, lswi_crudo)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (id_parcela, fecha) DO UPDATE SET
            evi_crudo  = COALESCE(series_diarias_vpm.evi_crudo, excluded.evi_crudo),
            lswi_crudo = COALESCE(series_diarias_vpm.lswi_crudo, excluded.lswi_crudo)
    """

    with closing(get_connection_raw()) as conn:
        with conn:
            if mode == "replace":
                parcelas = merged["id_parcela"].unique().tolist()
                fecha_min, fecha_max = merged["fecha"].min(), merged["fecha"].max()
                placeholders = ",".join(["?"] * len(parcelas))
                conn.execute(
                    f"""DELETE FROM series_diarias_vpm
                        WHERE id_parcela IN ({placeholders})
                          AND fecha BETWEEN ? AND ?""",
                    (*parcelas, fecha_min, fecha_max),
                )
                print(f"🗑️  {len(parcelas)} parcela(s) reiniciadas en el rango {fecha_min}–{fecha_max} (mode='replace').")
            conn.executemany(sql_upsert, rows)

    n = len(rows)
    print(f"✅  {n} filas escritas en 'series_diarias_vpm' (mode='{mode}').")
    return n


def guardar_datos_climaticos(
    dfs: dict[str, pd.DataFrame],
    ids_parcelas: list[int] | None = None,
    mode: Literal["replace", "append"] = "append",
) -> int:
    """
    Persiste el resultado de ``obtener_datos_climaticos_crudo`` en la tabla
    ``series_diarias_vpm`` del GeoPackage SQLite.

    Solo escribe ``temperatura_diaria_promedio`` y ``radiacion_total_promedio``.
    Nunca toca ``evi_crudo``, ``lswi_crudo`` ni ``gpp_diario``.

    AgERA5 tiene resolución ~11 km, por lo que todas las parcelas comparten
    la misma serie temporal.  Las columnas de los DataFrames de entrada se
    llaman ``Parcela_1``, ``Parcela_2``, ... (broadcast desde ingesta.py).
    El parámetro ``ids_parcelas`` permite mapear esas columnas a los
    ``id_parcela`` reales del GeoPackage; si se omite, se infieren como
    índice 0-based a partir del nombre de columna (``Parcela_N → N-1``).

    Parámetros
    ----------
    dfs : dict[str, pd.DataFrame]
        Resultado directo de ``obtener_datos_climaticos_crudo``.
        Se esperan las claves ``"temperature-mean"`` y ``"solar-radiation-flux"``.
        Cada DataFrame tiene DatetimeIndex y columnas ``Parcela_1…N``.
    ids_parcelas : list[int] | None
        Lista de ``id_parcela`` reales en el mismo orden que las columnas
        de los DataFrames (``Parcela_1`` → ``ids_parcelas[0]``, etc.).
        Si es ``None``, se usa ``Parcela_N - 1`` como id (0-based).
    mode : {"append", "replace"}
        - ``"append"`` (defecto): upsert por (id_parcela, fecha). Si la fila
          ya existe, los campos climáticos solo se rellenan si están en NULL
          (inmutabilidad del crudo).
        - ``"replace"``: elimina las filas existentes de las parcelas y el
          rango de fechas presente en ``dfs`` antes de insertar.

    Retorna
    -------
    int
        Número de filas escritas en la base de datos.

    Raises
    ------
    KeyError
        Si el dict no contiene ``"temperature-mean"`` o ``"solar-radiation-flux"``.
    ValueError
        Si ambos DataFrames están vacíos o no tienen índice de fechas válido.
    """
    clave_temp = "temperature-mean"
    clave_rad  = "solar-radiation-flux"

    if clave_temp not in dfs or clave_rad not in dfs:
        raise KeyError(
            f"El dict debe contener '{clave_temp}' y '{clave_rad}'. "
            f"Claves recibidas: {list(dfs.keys())}"
        )

    df_temp = dfs[clave_temp].copy()
    df_rad  = dfs[clave_rad].copy()

    if df_temp.empty and df_rad.empty:
        raise ValueError("Ambos DataFrames están vacíos; no hay datos que persistir.")

    for df in (df_temp, df_rad):
        df.index = pd.to_datetime(df.index).normalize()
        df.index.name = "fecha"

    def _a_largo(df: pd.DataFrame, col_valor: str) -> pd.DataFrame:
        """Pivota de ancho a largo y resuelve id_parcela real."""
        largo = (
            df.reset_index()
              .melt(id_vars="fecha", var_name="parcela_col", value_name=col_valor)
        )
        # Columnas esperadas: "Parcela_1", "Parcela_2", ...
        n_extraido = largo["parcela_col"].str.extract(r"^Parcela_(\d+)$")[0]

        if n_extraido.isna().any():
            problematicas = largo.loc[n_extraido.isna(), "parcela_col"].unique().tolist()
            raise ValueError(
                f"No se pudo extraer el índice de parcela de las columnas: {problematicas}. "
                f"Se esperan nombres con formato 'Parcela_N'."
            )

        # Convertir a id_parcela: si el usuario proveyó la lista, mapear;
        # si no, usar N-1 (índice 0-based coincidente con la tabla).
        if ids_parcelas is not None:
            n_to_id = {str(i + 1): pid for i, pid in enumerate(ids_parcelas)}
            id_series = n_extraido.map(n_to_id)
            if id_series.isna().any():
                raise ValueError(
                    "``ids_parcelas`` no cubre todos los índices de columna del DataFrame."
                )
            largo["id_parcela"] = id_series.astype(int)
        else:
            largo["id_parcela"] = n_extraido.astype(int) - 1

        return largo[["id_parcela", "fecha", col_valor]]

    largo_temp = _a_largo(df_temp, "temperatura_diaria_promedio")
    largo_rad  = _a_largo(df_rad,  "radiacion_total_promedio")

    merged = pd.merge(largo_temp, largo_rad, on=["id_parcela", "fecha"], how="outer")
    merged["fecha"] = merged["fecha"].dt.strftime("%Y-%m-%d")

    cols = ["id_parcela", "fecha", "temperatura_diaria_promedio", "radiacion_total_promedio"]
    df_rows = merged[cols].astype(object)
    df_rows = df_rows.where(pd.notna(df_rows), None)
    rows = list(df_rows.itertuples(index=False, name=None))

    if not rows:
        print("⚠️  Sin filas para escribir.")
        return 0

    sql_upsert = """
        INSERT INTO series_diarias_vpm
            (id_parcela, fecha, temperatura_diaria_promedio, radiacion_total_promedio)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (id_parcela, fecha) DO UPDATE SET
            temperatura_diaria_promedio = COALESCE(
                series_diarias_vpm.temperatura_diaria_promedio,
                excluded.temperatura_diaria_promedio
            ),
            radiacion_total_promedio = COALESCE(
                series_diarias_vpm.radiacion_total_promedio,
                excluded.radiacion_total_promedio
            )
    """

    with closing(get_connection_raw()) as conn:
        with conn:
            if mode == "replace":
                parcelas = merged["id_parcela"].unique().tolist()
                fecha_min, fecha_max = merged["fecha"].min(), merged["fecha"].max()
                placeholders = ",".join(["?"] * len(parcelas))
                conn.execute(
                    f"""DELETE FROM series_diarias_vpm
                        WHERE id_parcela IN ({placeholders})
                          AND fecha BETWEEN ? AND ?""",
                    (*parcelas, fecha_min, fecha_max),
                )
                print(
                    f"🗑️  {len(parcelas)} parcela(s) reiniciadas en el rango "
                    f"{fecha_min}–{fecha_max} (mode='replace')."
                )
            conn.executemany(sql_upsert, rows)

    n = len(rows)
    print(f"✅  {n} filas escritas en 'series_diarias_vpm' (campos climáticos, mode='{mode}').")
    return n

