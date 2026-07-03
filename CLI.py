#!/usr/bin/env python
# CLI.py — Menú interactivo para el pipeline de maíz Comayagua
"""
Punto de entrada único para operar el pipeline desde la terminal sin
tener que recordar nombres de funciones ni rutas.

Uso:
    python CLI.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import textwrap
from contextlib import closing
from pathlib import Path

# ── Asegurar que el root del proyecto esté en sys.path ────────────────────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import GPKG_PATH, CICLOS

# ══════════════════════════════════════════════════════════════════════════════
# Helpers de UI
# ══════════════════════════════════════════════════════════════════════════════

SEP  = "─" * 60
SEP2 = "═" * 60

def _clear() -> None:
    import os
    os.system("cls" if os.name == "nt" else "clear")

def _titulo(texto: str) -> None:
    print(f"\n{SEP2}\n  {texto}\n{SEP2}")

def _seccion(texto: str) -> None:
    print(f"\n{SEP}\n  {texto}\n{SEP}")

def _ok(msg: str)    -> None: print(f"  ✅  {msg}")
def _warn(msg: str)  -> None: print(f"  ⚠️   {msg}")
def _error(msg: str) -> None: print(f"  ❌  {msg}")
def _info(msg: str)  -> None: print(f"  ℹ️   {msg}")

def _pedir(prompt: str, default: str = "") -> str:
    sufijo = f" [{default}]" if default else ""
    val = input(f"  → {prompt}{sufijo}: ").strip()
    return val if val else default

def _menu(opciones: dict[str, str]) -> str:
    """Imprime un menú numerado y devuelve la clave elegida."""
    items = list(opciones.items())
    for i, (_, label) in enumerate(items, 1):
        print(f"  [{i}] {label}")
    print(f"  [0] Volver / Salir")
    while True:
        raw = input("\n  Opción: ").strip()
        if raw == "0":
            return "0"
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1][0]
        _warn("Opción inválida.")

def _pausar() -> None:
    input("\n  Presiona Enter para continuar…")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de datos
# ══════════════════════════════════════════════════════════════════════════════

def _get_conn() -> sqlite3.Connection:
    from utils.conexionDB import get_connection_raw
    return get_connection_raw()

def _cargar_geojson_parcelas() -> dict:
    """Lee parcelas vigentes del gpkg y retorna GeoJSON dict."""
    import geopandas as gpd
    from config import LAYERS_GPKG
    gdf = gpd.read_file(str(GPKG_PATH), layer=LAYERS_GPKG["parcelas"])
    gdf = gdf.to_crs("EPSG:4326")
    return json.loads(gdf.to_json())

def _conectar_openeo_cdse() -> object:
    """Conexión al backend CDSE — Sentinel-2, índices espectrales."""
    import openeo
    from config import OPENEO
    _info(f"Conectando a CDSE ({OPENEO})…")
    conn = openeo.connect(f"https://{OPENEO}").authenticate_oidc()
    _ok("Conexión CDSE establecida.")
    return conn

def _conectar_openeo_fed() -> object:
    """Conexión al backend federado — AgERA5, datos climáticos."""
    import openeo
    from config import OPENEOFED
    _info(f"Conectando a backend federado ({OPENEOFED})…")
    conn = openeo.connect(f"https://{OPENEOFED}").authenticate_oidc()
    _ok("Conexión federada establecida.")
    return conn

def _normalizar_fecha(raw: str) -> str:
    """
    Parsea y normaliza cualquier entrada de fecha razonable a 'YYYY-MM-DD'.

    Acepta, entre otros:
        '2025-5-1'   → '2025-05-01'
        '2025/05/01' → '2025-05-01'
        '20250501'   → '2025-05-01'
        '01-05-2025' → lo intenta con dayfirst como fallback

    Raises ValueError con mensaje amigable si no puede parsear.
    """
    import pandas as pd
    raw = raw.strip()
    if not raw:
        raise ValueError("La fecha no puede estar vacía.")

    # Intentos en orden de preferencia (más específico → menos)
    intentos = [
        dict(format="%Y-%m-%d"),
        dict(format="%Y/%m/%d"),
        dict(format="%Y%m%d"),
        dict(dayfirst=False),   # pandas heurístico, año primero
        dict(dayfirst=True),    # pandas heurístico, día primero
    ]
    for kwargs in intentos:
        try:
            ts = pd.to_datetime(raw, **kwargs)
            return ts.strftime("%Y-%m-%d")
        except Exception:
            continue

    raise ValueError(
        f"No se puede interpretar '{raw}' como fecha. "
        "Usa el formato YYYY-MM-DD (ej: 2025-05-01)."
    )


def _pedir_fecha(prompt: str, default: str) -> str:
    """Pide una fecha al usuario repitiendo hasta obtener una válida."""
    while True:
        raw = _pedir(prompt, default)
        try:
            normalizada = _normalizar_fecha(raw)
            if normalizada != raw:
                _info(f"Fecha normalizada: {raw!r} → {normalizada!r}")
            return normalizada
        except ValueError as exc:
            _error(str(exc))


def _pedir_fechas(ciclo_default: str = "primera") -> tuple[str, str]:
    defaults = {
        "primera":  ("2025-05-01", "2025-10-30"),
        "postrera": ("2025-08-01", "2026-01-31"),
    }
    d_ini, d_fin = defaults.get(ciclo_default, ("2025-05-01", "2025-10-30"))
    fecha_inicio = _pedir_fecha("Fecha inicio (YYYY-MM-DD)", d_ini)
    fecha_fin    = _pedir_fecha("Fecha fin   (YYYY-MM-DD)", d_fin)
    return fecha_inicio, fecha_fin


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 1 — Gestión de parcelas
# ══════════════════════════════════════════════════════════════════════════════

def _menu_parcelas() -> None:
    while True:
        _seccion("1 · Gestión de Parcelas")
        key = _menu({
            "seed_geojson": "Inicializar BD desde GeoJSON de parcelas",
            "seed_gpkg":    "Inicializar BD desde GeoPackage existente",
            "append":       "Agregar nuevas parcelas (append)",
            "delinear":     "Correr Delineate-Anything (segmentación automática)",
            "ver":          "Ver parcelas en la BD",
        })
        if key == "0":
            return
        elif key == "seed_geojson":
            _accion_seed_geojson()
        elif key == "seed_gpkg":
            _accion_seed_gpkg()
        elif key == "append":
            _accion_append_parcelas()
        elif key == "delinear":
            _accion_delinear()
        elif key == "ver":
            _accion_ver_parcelas()

def _accion_seed_geojson() -> None:
    _seccion("Inicializar BD desde GeoJSON")
    ruta = _pedir("Ruta al GeoJSON", str(ROOT / "data" / "PoligonosMaizPlayitas.geojson"))
    if not Path(ruta).exists():
        _error(f"Archivo no encontrado: {ruta}")
        _pausar(); return
    from utils.db import seeding
    seeding(ruta)
    _ok("Seeding completado.")
    _pausar()

def _accion_seed_gpkg() -> None:
    _seccion("Inicializar BD desde GeoPackage")
    ruta = _pedir("Ruta al .gpkg de origen")
    if not Path(ruta).exists():
        _error(f"Archivo no encontrado: {ruta}")
        _pausar(); return
    capa = _pedir("Nombre de la capa (Enter = primera disponible)", "")
    from utils.db import actualizar_gpkg
    actualizar_gpkg(
        data=ruta,
        mode="replace",
        source_layer=capa if capa else None,
    )
    _ok("Parcelas actualizadas.")
    _pausar()

def _accion_append_parcelas() -> None:
    _seccion("Agregar parcelas")
    ruta = _pedir("Ruta al archivo (GeoJSON / gpkg / shp)")
    if not Path(ruta).exists():
        _error(f"Archivo no encontrado: {ruta}")
        _pausar(); return
    from utils.db import actualizar_gpkg
    kw: dict = {}
    if ruta.lower().endswith(".gpkg"):
        capa = _pedir("Capa de origen (Enter = primera)", "")
        if capa:
            kw["source_layer"] = capa
    actualizar_gpkg(data=ruta, mode="append", **kw)
    _ok("Parcelas agregadas.")
    _pausar()

def _accion_delinear() -> None:
    _seccion("Delineate-Anything — segmentación automática")
    _warn("Este es un proceso pesado, tardará aprox. 1 hora")
    confirmar = _pedir("¿Continuar? (s/n)", "n")
    if confirmar.lower() != "s":
        return
    from pipeline.modulo_parcelas import ejecutar_delineate_anything_local
    ejecutar_delineate_anything_local()
    _ok("Delineación completada.")
    _pausar()

def _accion_ver_parcelas() -> None:
    _seccion("Parcelas en la BD")
    try:
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT id_parcela, ROUND(area_ha,4) AS area_ha FROM parcelas_vigentes ORDER BY id_parcela LIMIT 50;"
            ).fetchall()
        if not rows:
            _warn("La tabla parcelas_vigentes está vacía o no existe.")
        else:
            print(f"\n  {'id_parcela':>12}  {'area_ha':>10}")
            print(f"  {'─'*12}  {'─'*10}")
            for r in rows:
                print(f"  {r[0]:>12}  {r[1]:>10}")
            _info(f"Mostrando {len(rows)} fila(s).")
    except Exception as exc:
        _error(str(exc))
    _pausar()


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 2 — Ingesta satelital y climática
# ══════════════════════════════════════════════════════════════════════════════

def _menu_ingesta() -> None:
    while True:
        _seccion("2 · Ingesta de Datos (openEO)")
        key = _menu({
            "indices": "Descargar índices EVI/LSWI (Sentinel-2)",
            "clima":   "Descargar datos climáticos (AgERA5)",
            "ambos":   "Descargar ambos y guardar en BD",
        })
        if key == "0":
            return
        elif key == "indices":
            _accion_ingesta_indices()
        elif key == "clima":
            _accion_ingesta_clima()
        elif key == "ambos":
            _accion_ingesta_completa()

def _accion_ingesta_indices() -> None:
    _seccion("Descarga de índices EVI/LSWI  [backend: CDSE]")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()
    conn = _conectar_openeo_cdse()
    from pipeline.ingesta import obtener_datacube_indices_crudo
    from utils.db import guardar_indices_crudos
    dfs = obtener_datacube_indices_crudo(conn, geojson, fecha_inicio, fecha_fin)
    n = guardar_indices_crudos(dfs)
    _ok(f"Guardadas {n} filas en series_diarias_vpm.")
    _pausar()

def _accion_ingesta_clima() -> None:
    _seccion("Descarga de datos climáticos AgERA5  [backend: federado]")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()
    conn = _conectar_openeo_fed()
    from pipeline.ingesta import obtener_datos_climaticos_crudo
    dfs = obtener_datos_climaticos_crudo(conn, geojson, fecha_inicio, fecha_fin)
    for banda, df in dfs.items():
        _ok(f"{banda}: {df.shape[0]} fechas × {df.shape[1]} parcelas")
    _info("Datos climáticos en memoria. Úsalos con calcular_rendimiento_desde_indices.")
    _pausar()

def _accion_ingesta_completa() -> None:
    _seccion("Descarga completa (índices + clima) y guardado en BD")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()

    # Índices → CDSE
    conn_cdse = _conectar_openeo_cdse()
    from pipeline.ingesta import obtener_datacube_indices_crudo, obtener_datos_climaticos_crudo
    from utils.db import guardar_indices_crudos
    dfs_indices = obtener_datacube_indices_crudo(conn_cdse, geojson, fecha_inicio, fecha_fin)
    n = guardar_indices_crudos(dfs_indices)
    _ok(f"Índices guardados: {n} filas.")

    # Clima → federado
    conn_fed = _conectar_openeo_fed()
    dfs_clima = obtener_datos_climaticos_crudo(conn_fed, geojson, fecha_inicio, fecha_fin)
    _ok(f"Clima descargado: {list(dfs_clima.keys())}")
    _pausar()

def _elegir_ciclo() -> str:
    print("\n  Ciclo de cultivo:")
    opciones = list(CICLOS.values())
    for i, c in enumerate(opciones, 1):
        print(f"  [{i}] {c}")
    raw = input("  Opción [1]: ").strip() or "1"
    idx = int(raw) - 1 if raw.isdigit() else 0
    return opciones[min(idx, len(opciones) - 1)]


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 3 — Motor de predicción
# ══════════════════════════════════════════════════════════════════════════════

def _menu_prediccion() -> None:
    while True:
        _seccion("3 · Motor de Predicción")
        key = _menu({
            "completo":  "Pipeline completo (descarga + predicción)",
            "memoria":   "Predicción desde índices en BD (sin re-descargar)",
        })
        if key == "0":
            return
        elif key == "completo":
            _accion_pipeline_completo()
        elif key == "memoria":
            _accion_pipeline_desde_bd()

def _accion_pipeline_completo() -> None:
    _seccion("Pipeline completo end-to-end")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()
    conn_cdse = _conectar_openeo_cdse()
    conn_fed  = _conectar_openeo_fed()
    from pipeline.motor_prediccion import ejecutar_pipeline_completo
    resultados = ejecutar_pipeline_completo(
        connection=conn_cdse,
        connection_fed=conn_fed,
        geojson_openeo=geojson,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )
    _mostrar_resumen_rendimiento(resultados["rendimiento"])
    _pausar()

def _accion_pipeline_desde_bd() -> None:
    _seccion("Predicción desde índices en BD")
    _info("Cargando índices crudos desde series_diarias_vpm…")

    # Reconstruir dfs_crudos desde la BD
    try:
        with closing(_get_conn()) as conn:
            import pandas as pd
            df_raw = pd.read_sql(
                "SELECT id_parcela, fecha, evi_crudo, lswi_crudo FROM series_diarias_vpm ORDER BY fecha;",
                conn,
                parse_dates=["fecha"],
            )
    except Exception as exc:
        _error(f"No se pudo leer la BD: {exc}")
        _pausar(); return

    if df_raw.empty:
        _warn("No hay datos en series_diarias_vpm. Corre la ingesta primero.")
        _pausar(); return

    import pandas as pd
    df_raw["parcela_col"] = "id_" + df_raw["id_parcela"].astype(str)
    df_evi  = df_raw.pivot(index="fecha", columns="parcela_col", values="evi_crudo")
    df_lswi = df_raw.pivot(index="fecha", columns="parcela_col", values="lswi_crudo")
    dfs_crudos = {"EVI": df_evi, "LSWI": df_lswi}

    _ok(f"Índices cargados: {df_evi.shape[0]} fechas × {df_evi.shape[1]} parcelas.")

    # Pedir fechas y datos climáticos
    fecha_inicio = str(df_evi.index.min().date())
    fecha_fin    = _pedir("Fecha fin (YYYY-MM-DD)", str(df_evi.index.max().date()))

    _info("Se necesitan datos climáticos (AgERA5). Conectando al backend federado…")
    geojson = _cargar_geojson_parcelas()
    conn_fed = _conectar_openeo_fed()
    from pipeline.ingesta import obtener_datos_climaticos_crudo
    dfs_clima = obtener_datos_climaticos_crudo(conn_fed, geojson, fecha_inicio, fecha_fin)

    from pipeline.motor_prediccion import calcular_rendimiento_desde_indices
    resultados = calcular_rendimiento_desde_indices(
        dfs_crudos=dfs_crudos,
        dfs_clima=dfs_clima,
        fecha_fin=fecha_fin,
        fecha_inicio=fecha_inicio,
    )
    _mostrar_resumen_rendimiento(resultados["rendimiento"])
    _pausar()

def _mostrar_resumen_rendimiento(rendimiento: dict) -> None:
    _seccion("Resultados de rendimiento")
    yield_tha = rendimiento.get("yield_final_tha")
    if yield_tha is None:
        _warn("No hay datos de rendimiento.")
        return
    print(f"\n  {'Parcela':<20}  {'t/ha':>8}  {'qq/ha':>8}")
    print(f"  {'─'*20}  {'─'*8}  {'─'*8}")
    for parcela, val in yield_tha.items():
        print(f"  {str(parcela):<20}  {val:>8.3f}  {val*22.0458:>8.1f}")
    print(f"\n  Promedio:  {yield_tha.mean():.3f} t/ha  |  {yield_tha.mean()*22.0458:.1f} qq/ha")


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 4 — Inspección de la base de datos
# ══════════════════════════════════════════════════════════════════════════════

def _menu_bd() -> None:
    while True:
        _seccion("4 · Inspección de la Base de Datos")
        key = _menu({
            "tablas":     "Listar tablas y conteos",
            "series":     "Ver series_diarias_vpm",
            "produccion": "Ver produccion_acumulada_ciclo",
            "parcelas":   "Ver parcelas_vigentes",
            "sql":        "Ejecutar SQL personalizado",
            "limpiar":    "Limpiar tabla series_diarias_vpm",
        })
        if key == "0":
            return
        elif key == "tablas":
            _accion_listar_tablas()
        elif key == "series":
            _accion_ver_tabla("series_diarias_vpm", limit=30)
        elif key == "produccion":
            _accion_ver_tabla("produccion_acumulada_ciclo", limit=30)
        elif key == "parcelas":
            _accion_ver_parcelas()
        elif key == "sql":
            _accion_sql_libre()
        elif key == "limpiar":
            _accion_limpiar_series()

def _accion_listar_tablas() -> None:
    _seccion("Tablas en la BD")
    try:
        with closing(_get_conn()) as conn:
            tablas = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
            ).fetchall()
        print()
        for (t,) in tablas:
            try:
                with closing(_get_conn()) as conn2:
                    n = conn2.execute(f"SELECT COUNT(*) FROM \"{t}\";").fetchone()[0]
                print(f"  • {t:<40}  {n:>8} filas")
            except Exception:
                print(f"  • {t:<40}  (no contable)")
    except Exception as exc:
        _error(str(exc))
    _pausar()

def _accion_ver_tabla(tabla: str, limit: int = 30) -> None:
    _seccion(f"Tabla: {tabla}  (primeras {limit} filas)")
    try:
        import pandas as pd
        with closing(_get_conn()) as conn:
            df = pd.read_sql(f"SELECT * FROM \"{tabla}\" LIMIT {limit};", conn)
        if df.empty:
            _warn("La tabla está vacía.")
        else:
            # Ajustar ancho de display
            pd.set_option("display.max_columns", None)
            pd.set_option("display.width", 120)
            pd.set_option("display.float_format", "{:.4f}".format)
            print()
            print(df.to_string(index=False))
            _info(f"{len(df)} fila(s) mostradas.")
    except Exception as exc:
        _error(str(exc))
    _pausar()

def _accion_sql_libre() -> None:
    _seccion("SQL personalizado")
    print("  (escribe la query en una línea; Enter vacío cancela)")
    sql = input("  SQL> ").strip()
    if not sql:
        return
    try:
        import pandas as pd
        with closing(_get_conn()) as conn:
            if sql.strip().upper().startswith("SELECT"):
                df = pd.read_sql(sql, conn)
                print()
                print(df.to_string(index=False) if not df.empty else "(sin resultados)")
            else:
                conn.execute(sql)
                conn.commit()
                _ok("Sentencia ejecutada.")
    except Exception as exc:
        _error(str(exc))
    _pausar()

def _accion_limpiar_series() -> None:
    _seccion("Limpiar series_diarias_vpm")
    _warn("Esto elimina TODAS las filas de series_diarias_vpm. No hay rollback.")
    confirmar = _pedir("Escribe CONFIRMAR para continuar", "")
    if confirmar != "CONFIRMAR":
        _info("Operación cancelada.")
        _pausar(); return
    try:
        with closing(_get_conn()) as conn:
            conn.execute("DELETE FROM series_diarias_vpm;")
            conn.commit()
        _ok("Tabla vaciada.")
    except Exception as exc:
        _error(str(exc))
    _pausar()


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 5 — Diagnóstico del proyecto
# ══════════════════════════════════════════════════════════════════════════════

def _menu_diagnostico() -> None:
    while True:
        _seccion("5 · Diagnóstico del Proyecto")
        key = _menu({
            "rutas":   "Verificar rutas y archivos del proyecto",
            "gpkg":    "Verificar integridad del GeoPackage",
            "version": "Mostrar versiones de dependencias clave",
        })
        if key == "0":
            return
        elif key == "rutas":
            _accion_verificar_rutas()
        elif key == "gpkg":
            _accion_verificar_gpkg()
        elif key == "version":
            _accion_versiones()

def _accion_verificar_rutas() -> None:
    _seccion("Verificación de rutas")
    from config import GPKG_PATH, MUNICIPIO_GEOJSON

    checks = [
        ("GeoPackage (BD)",     Path(GPKG_PATH)),
        ("GeoJSON parcelas",    ROOT / "data" / "PoligonosMaizPlayitas.geojson"),
        ("GeoJSON Valle",       ROOT / "data" / "ValleComayagua.geojson"),
        ("Delineate script",    ROOT / "delineate_anything" / "delineate.py"),
        ("Delineate .venv",     ROOT / "delineate_anything" / ".venv" / "Scripts" / "python.exe"),
    ]
    print()
    for label, ruta in checks:
        estado = "✅" if ruta.exists() else "❌  NO ENCONTRADO"
        print(f"  {estado}  {label:<30}  {ruta}")
    _pausar()

def _accion_verificar_gpkg() -> None:
    _seccion("Integridad del GeoPackage")
    ruta = Path(GPKG_PATH)
    if not ruta.exists():
        _error(f"No existe: {ruta}")
        _pausar(); return
    size_kb = ruta.stat().st_size / 1024
    _info(f"Tamaño: {size_kb:.1f} KB")
    try:
        with closing(_get_conn()) as conn:
            result = conn.execute("PRAGMA integrity_check;").fetchone()
            if result and result[0] == "ok":
                _ok("PRAGMA integrity_check: OK")
            else:
                _warn(f"PRAGMA integrity_check: {result}")
            tablas = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
            _info(f"Tablas encontradas: {[t[0] for t in tablas]}")
    except Exception as exc:
        _error(str(exc))
    _pausar()

def _accion_versiones() -> None:
    _seccion("Versiones de dependencias")
    libs = [
        "geopandas", "pandas", "numpy", "openeo",
        "fiona", "pyogrio", "streamlit", "folium",
    ]
    print()
    for lib in libs:
        try:
            mod = __import__(lib)
            ver = getattr(mod, "__version__", "?")
            print(f"  ✅  {lib:<18} {ver}")
        except ImportError:
            print(f"  ❌  {lib:<18} no instalado")
    _pausar()


# ══════════════════════════════════════════════════════════════════════════════
# MENÚ PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

_MENU_PRINCIPAL = {
    "parcelas":    "Gestión de parcelas vigentes",
    "ingesta":     "Ingesta satelital y climática (openEO)",
    "prediccion":  "Motor de predicción de rendimiento",
    "bd":          "Inspección de la base de datos SQLite",
    "diagnostico": "Diagnóstico del proyecto",
}

def main() -> None:
    while True:
        _clear()
        _titulo("🌽  Pipeline Maíz Comayagua — CLI")
        print(textwrap.dedent(f"""
          GeoPackage : {GPKG_PATH}
          Python     : {sys.executable}
        """))
        key = _menu(_MENU_PRINCIPAL)
        if key == "0":
            print("\n  Hasta luego.\n")
            sys.exit(0)
        elif key == "parcelas":
            _menu_parcelas()
        elif key == "ingesta":
            _menu_ingesta()
        elif key == "prediccion":
            _menu_prediccion()
        elif key == "bd":
            _menu_bd()
        elif key == "diagnostico":
            _menu_diagnostico()


if __name__ == "__main__":
    main()
