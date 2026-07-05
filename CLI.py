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

import pandas as pd

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
    _warn("Este es un proceso pesado, tardará aprox. 1 hora ejecutándose con CPU")
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
            "indices": "Sincronizar índices EVI/LSWI (BD + openEO si hay gaps)",
            "clima":   "Sincronizar datos climáticos AgERA5 (BD + openEO si hay gaps)",
            "ambos":   "Sincronizar ambos (índices + clima)",
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
    _seccion("Sincronizar índices EVI/LSWI  [BD primero → openEO solo para gaps]")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()
    conn = _conectar_openeo_cdse()
    from pipeline.ingesta import obtener_indices
    dfs = obtener_indices(conn, geojson, fecha_inicio, fecha_fin)
    df_evi = dfs["EVI"]
    _ok(f"Índices listos: {df_evi.shape[0]} fechas × {df_evi.shape[1]} parcelas.")
    _pausar()

def _accion_ingesta_clima() -> None:
    _seccion("Sincronizar datos climáticos AgERA5  [BD primero → openEO solo para gaps]")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()
    conn = _conectar_openeo_fed()
    from pipeline.ingesta import obtener_clima
    dfs = obtener_clima(conn, geojson, fecha_inicio, fecha_fin)
    df_t = dfs["temperature-mean"]
    _ok(f"Clima listo: {df_t.shape[0]} fechas × {df_t.shape[1]} parcelas.")
    _pausar()

def _accion_ingesta_completa() -> None:
    _seccion("Sincronizar índices + clima (BD primero → openEO solo para gaps)")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()

    from pipeline.ingesta import obtener_indices, obtener_clima

    conn_cdse = _conectar_openeo_cdse()
    dfs_indices = obtener_indices(conn_cdse, geojson, fecha_inicio, fecha_fin)
    _ok(f"Índices listos: {dfs_indices['EVI'].shape[0]} fechas × {dfs_indices['EVI'].shape[1]} parcelas.")

    conn_fed = _conectar_openeo_fed()
    dfs_clima = obtener_clima(conn_fed, geojson, fecha_inicio, fecha_fin)
    _ok(f"Clima listo: {dfs_clima['temperature-mean'].shape[0]} fechas × {dfs_clima['temperature-mean'].shape[1]} parcelas.")
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
            "completo":  "Pipeline completo (BD + openEO para gaps)",
            "desde_bd":  "Pipeline desde BD (sin openEO, re-procesa ciclo ya ingestado)",
            "memoria":   "Núcleo desde índices en memoria",
        })
        if key == "0":
            return
        elif key == "completo":
            _accion_pipeline_completo()
        elif key == "desde_bd":
            _accion_pipeline_desde_bd()
        elif key == "memoria":
            _accion_pipeline_desde_memoria()

def _accion_pipeline_completo() -> None:
    _seccion("Pipeline completo  [BD + openEO para gaps]")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    geojson = _cargar_geojson_parcelas()
    conn_cdse = _conectar_openeo_cdse()
    conn_fed  = _conectar_openeo_fed()
    from pipeline.flujos_trabajo import ejecutar_pipeline_completo
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
    _seccion("Pipeline desde BD  [sin conexión openEO]")
    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)
    from pipeline.flujos_trabajo import ejecutar_pipeline_desde_bd
    try:
        resultados = ejecutar_pipeline_desde_bd(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
        )
    except ValueError as exc:
        _error(str(exc))
        _info("Usa la opción 'Pipeline completo' para descargar los datos faltantes.")
        _pausar(); return
    _mostrar_resumen_rendimiento(resultados["rendimiento"])
    _pausar()

def _accion_pipeline_desde_memoria() -> None:
    _seccion("Núcleo desde índices en memoria")
    _info("Cargando índices crudos desde series_diarias_vpm…")

    try:
        from pipeline.ingesta import cargar_indices_desde_bd
        dfs_crudos = cargar_indices_desde_bd()
    except ValueError as exc:
        _warn(str(exc))
        _pausar(); return
    except Exception as exc:
        _error(f"No se pudo leer la BD: {exc}")
        _pausar(); return

    df_evi = dfs_crudos["EVI"]
    _ok(f"Índices cargados: {df_evi.shape[0]} fechas × {df_evi.shape[1]} parcelas.")

    fecha_inicio = str(df_evi.index.min().date())
    fecha_fin    = _pedir("Fecha fin (YYYY-MM-DD)", str(df_evi.index.max().date()))

    _info("Sincronizando datos climáticos (BD primero → openEO solo si hay gaps)…")
    geojson  = _cargar_geojson_parcelas()
    conn_fed = _conectar_openeo_fed()
    from pipeline.ingesta import obtener_clima
    dfs_clima = obtener_clima(conn_fed, geojson, fecha_inicio, fecha_fin)

    from pipeline.flujos_trabajo import calcular_rendimiento_desde_indices
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
# SECCIÓN 6 — Módulo Fenológico
# ══════════════════════════════════════════════════════════════════════════════

def _menu_fenologico() -> None:
    while True:
        _seccion("6 · Módulo Fenológico")
        key = _menu({
            "sos_parcela":  "Detectar SOS para una parcela específica",
            "sos_todas":    "Detectar SOS para todas las parcelas",
            "ver_sos_bd":   "Ver SOS guardados en produccion_acumulada_ciclo",
            "ciclos":       "Segmentar ciclos en serie multi-anual (experimental)",
        })
        if key == "0":
            return
        elif key == "sos_parcela":
            _accion_sos_parcela()
        elif key == "sos_todas":
            _accion_sos_todas()
        elif key == "ver_sos_bd":
            _accion_ver_sos_bd()
        elif key == "ciclos":
            _accion_segmentar_ciclos()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos del módulo fenológico
# ─────────────────────────────────────────────────────────────────────────────

def _listar_parcelas_disponibles() -> list[int]:
    """Devuelve lista de id_parcela con datos en series_diarias_vpm."""
    try:
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT id_parcela FROM series_diarias_vpm ORDER BY id_parcela;"
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _pedir_parcela(disponibles: list[int]) -> int | None:
    """Pide al usuario que seleccione un id_parcela de la lista disponible."""
    if not disponibles:
        _warn("No hay parcelas con datos en series_diarias_vpm.")
        return None
    print(f"\n  Parcelas disponibles: {disponibles}")
    raw = _pedir("id_parcela", str(disponibles[0]))
    try:
        pid = int(raw)
        if pid not in disponibles:
            _warn(f"id_parcela {pid} no está en la lista de disponibles.")
            return None
        return pid
    except ValueError:
        _error(f"'{raw}' no es un número entero válido.")
        return None


def _pedir_factor() -> float:
    """Pide el factor de amplitud para detectar_sos (0-1)."""
    while True:
        raw = _pedir("Factor de amplitud para SOS (0.0-1.0)", "0.2")
        try:
            f = float(raw)
            if 0.0 <= f <= 1.0:
                return f
            _warn("El factor debe estar entre 0.0 y 1.0.")
        except ValueError:
            _error(f"'{raw}' no es un número válido.")


def _pedir_ventana_sos_opcional(etiqueta: str = "ventana_sos") -> tuple | None:
    """
    Pide opcionalmente una ventana (fecha_ini, fecha_fin).
    Devuelve None si el usuario deja el campo vacío.
    """
    print(f"\n  {etiqueta} — deja en blanco para no restringir.")
    raw_ini = _pedir(f"  {etiqueta} inicio (YYYY-MM-DD)", "").strip()
    if not raw_ini:
        return None
    raw_fin = _pedir(f"  {etiqueta} fin   (YYYY-MM-DD)", "").strip()
    if not raw_fin:
        return None
    try:
        ini = _normalizar_fecha(raw_ini)
        fin = _normalizar_fecha(raw_fin)
        return (ini, fin)
    except ValueError as exc:
        _error(str(exc))
        return None


def _preparar_resultado_preprocesamiento(
    id_parcela: int,
    fecha_inicio: str,
    fecha_fin: str,
) -> dict | None:
    """
    Carga índices crudos desde la BD para una parcela y los preprocesa
    con Whittaker, devolviendo el dict listo para detectar_sos.
    """
    try:
        from pipeline.ingesta import cargar_indices_desde_bd
        dfs_crudos = cargar_indices_desde_bd(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            ids_parcelas=[id_parcela],
        )
    except ValueError as exc:
        _warn(str(exc))
        return None
    except Exception as exc:
        _error(f"Error leyendo BD: {exc}")
        return None

    try:
        from pipeline.modulo_vpm import preprocesar_indices_vpm
        resultado = preprocesar_indices_vpm(dfs_crudos)
    except Exception as exc:
        _error(f"Error en preprocesamiento Whittaker: {exc}")
        return None

    return resultado


def _mostrar_resultado_sos(res: dict, id_parcela: int | str = "") -> None:
    """Imprime en consola el resultado de detectar_sos de forma legible."""
    prefijo = f"  Parcela {id_parcela} — " if id_parcela != "" else "  "
    sos = res.get("sos_fecha")
    pos = res.get("pos_fecha")
    if sos is None:
        print(f"{prefijo}SOS no detectado  (amplitud={res.get('amplitud')})")
        return

    sos_str  = str(sos.date()) if hasattr(sos, "date") else str(sos)
    pos_str  = str(pos.date()) if hasattr(pos, "date") else str(pos)
    umbral   = res.get("umbral")
    amplitud = res.get("amplitud")
    print(
        f"{prefijo}"
        f"SOS={sos_str}  (val={res['sos_valor']:.4f})  |  "
        f"Pico={pos_str}  (val={res['pos_valor']:.4f})  |  "
        f"Umbral={umbral:.4f}  Amplitud={amplitud:.4f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Acciones del módulo fenológico
# ─────────────────────────────────────────────────────────────────────────────

def _accion_sos_parcela() -> None:
    """
    Detecta el Start of Season para una parcela específica.
    Permite definir ventana_busqueda y ventana_sos de forma interactiva.
    """
    _seccion("Detectar SOS — parcela específica")

    disponibles = _listar_parcelas_disponibles()
    id_parcela = _pedir_parcela(disponibles)
    if id_parcela is None:
        _pausar(); return

    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)

    indice = _pedir("Índice a usar (EVI / LSWI)", "EVI").upper()
    if indice not in ("EVI", "LSWI"):
        _warn("Índice no reconocido, usando EVI.")
        indice = "EVI"

    factor = _pedir_factor()

    _info("ventana_busqueda — restringe la búsqueda de pico y base al calendario del ciclo.")
    ventana_busqueda = _pedir_ventana_sos_opcional("ventana_busqueda")

    _info("ventana_sos — sub-rango donde se acepta el cruce del umbral (siembra + emergencia).")
    ventana_sos = _pedir_ventana_sos_opcional("ventana_sos")

    _info("Cargando y preprocesando índices desde BD…")
    resultado_prep = _preparar_resultado_preprocesamiento(id_parcela, fecha_inicio, fecha_fin)
    if resultado_prep is None:
        _pausar(); return

    col = f"id_{id_parcela}"
    df_indice = resultado_prep[indice]

    if col not in df_indice.columns:
        _error(f"Columna '{col}' no encontrada en el DataFrame de {indice}.")
        _pausar(); return

    serie  = df_indice[col].values
    fechas = df_indice.index

    from pipeline.modulo_fenologico import detectar_sos
    res = detectar_sos(
        serie=serie,
        fechas=fechas,
        factor=factor,
        ventana_busqueda=ventana_busqueda,
        ventana_sos=ventana_sos,
    )

    _seccion("Resultado SOS")
    _mostrar_resultado_sos(res, id_parcela)

    # Tabla completa de métricas
    print()
    print(f"  {'Campo':<18}  Valor")
    print(f"  {'─'*18}  {'─'*30}")
    for campo, val in res.items():
        if hasattr(val, "date"):
            val_str = str(val.date())
        elif isinstance(val, float):
            val_str = f"{val:.6f}"
        else:
            val_str = str(val) if val is not None else "—"
        print(f"  {campo:<18}  {val_str}")

    # Mostrar gráfico ASCII de la serie si es terminal que lo soporte
    _mostrar_grafico_ascii_serie(df_indice[col], res, indice)

    _pausar()


def _mostrar_grafico_ascii_serie(
    serie: "pd.Series",
    res: dict,
    indice: str = "EVI",
) -> None:
    """Dibuja un gráfico ASCII ligero de la serie con marcadores SOS y Pico."""
    try:
        import pandas as pd
        ancho = 60
        alto  = 10
        s = serie.dropna()
        if s.empty:
            return

        vmin, vmax = s.min(), s.max()
        rng = vmax - vmin if (vmax - vmin) > 0 else 1.0

        fechas_list = list(s.index)
        vals_list   = list(s.values)
        n = len(vals_list)
        paso = max(1, n // ancho)

        # Reducir a `ancho` puntos
        puntos_x = list(range(0, n, paso))[:ancho]
        puntos_v = [vals_list[i] for i in puntos_x]
        puntos_f = [fechas_list[i] for i in puntos_x]

        # Escalar a filas de alto
        def _escalar(v):
            return int((v - vmin) / rng * (alto - 1))

        lienzo = [[" "] * len(puntos_x) for _ in range(alto)]

        sos_col = pos_col = None
        for j, (f, v) in enumerate(zip(puntos_f, puntos_v)):
            fila = alto - 1 - _escalar(v)
            lienzo[fila][j] = "·"
            if res.get("sos_fecha") is not None:
                if abs((pd.Timestamp(f) - pd.Timestamp(res["sos_fecha"])).days) < paso * 2:
                    sos_col = j
            if res.get("pos_fecha") is not None:
                if abs((pd.Timestamp(f) - pd.Timestamp(res["pos_fecha"])).days) < paso * 2:
                    pos_col = j

        # Marcar SOS (S) y Pico (P)
        if sos_col is not None:
            fila_sos = alto - 1 - _escalar(res["sos_valor"])
            fila_sos = max(0, min(alto - 1, fila_sos))
            lienzo[fila_sos][sos_col] = "S"
        if pos_col is not None:
            fila_pos = alto - 1 - _escalar(res["pos_valor"])
            fila_pos = max(0, min(alto - 1, fila_pos))
            lienzo[fila_pos][pos_col] = "P"

        # Línea de umbral
        if res.get("umbral") is not None:
            fila_u = alto - 1 - _escalar(res["umbral"])
            fila_u = max(0, min(alto - 1, fila_u))
            for j in range(len(puntos_x)):
                if lienzo[fila_u][j] == " ":
                    lienzo[fila_u][j] = "-"

        print(f"\n  ── Gráfico {indice} (ASCII) ──  S=SOS  P=Pico  -=Umbral\n")
        print(f"  {vmax:.3f} ┐")
        for fila in lienzo:
            print("         │" + "".join(fila))
        print(f"  {vmin:.3f} └" + "─" * len(puntos_x))
        fecha_ini = fechas_list[0].strftime("%Y-%m-%d")
        fecha_fin = fechas_list[-1].strftime("%Y-%m-%d")
        print(f"          {fecha_ini}{' ' * (len(puntos_x) - 20)}{fecha_fin}")
    except Exception:
        pass  # el gráfico es decorativo; no romper el flujo si falla


def _accion_sos_todas() -> None:
    """
    Detecta SOS para todas las parcelas disponibles en la BD.
    Permite una ventana_sos global y muestra tabla resumen.
    """
    _seccion("Detectar SOS — todas las parcelas")

    ciclo = _elegir_ciclo()
    fecha_inicio, fecha_fin = _pedir_fechas(ciclo)

    indice = _pedir("Índice a usar (EVI / LSWI)", "EVI").upper()
    if indice not in ("EVI", "LSWI"):
        indice = "EVI"

    factor = _pedir_factor()

    _info("ventana_busqueda global — aplica a todas las parcelas (Enter = sin restricción).")
    ventana_busqueda = _pedir_ventana_sos_opcional("ventana_busqueda")

    _info("ventana_sos global — sub-rango de aceptación del cruce (Enter = sin restricción).")
    ventana_sos_global = _pedir_ventana_sos_opcional("ventana_sos")

    _info("Cargando índices de todas las parcelas desde BD…")
    try:
        from pipeline.ingesta import cargar_indices_desde_bd
        dfs_crudos = cargar_indices_desde_bd(
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
        )
    except ValueError as exc:
        _warn(str(exc))
        _pausar(); return
    except Exception as exc:
        _error(f"Error leyendo BD: {exc}")
        _pausar(); return

    _info("Preprocesando (Whittaker)…")
    try:
        from pipeline.modulo_vpm import preprocesar_indices_vpm
        resultado_prep = preprocesar_indices_vpm(dfs_crudos)
    except Exception as exc:
        _error(f"Error en preprocesamiento: {exc}")
        _pausar(); return

    from pipeline.modulo_fenologico import detectar_sos_por_parcela

    # ventana_sos se pasa dentro del helper; construimos ventanas_busqueda
    # como tuple global si el usuario la definió
    df_sos = detectar_sos_por_parcela(
        resultado_preprocesamiento=resultado_prep,
        indice=indice,
        factor=factor,
        ventanas_busqueda=ventana_busqueda,
    )

    # Aplicar ventana_sos post-proceso si se definió (detectar_sos_por_parcela
    # no la acepta globalmente, así que filtramos el resultado aquí)
    if ventana_sos_global is not None:
        ini_sos = pd.Timestamp(ventana_sos_global[0])
        fin_sos = pd.Timestamp(ventana_sos_global[1])
        fuera = (
            df_sos["sos_fecha"].notna() &
            ((df_sos["sos_fecha"] < ini_sos) | (df_sos["sos_fecha"] > fin_sos))
        )
        df_sos.loc[fuera, "sos_fecha"] = None
        df_sos.loc[fuera, "sos_valor"] = None
        if fuera.any():
            _warn(
                f"{fuera.sum()} parcela(s) con SOS fuera de ventana_sos "
                f"[{ventana_sos_global[0]} → {ventana_sos_global[1]}] → marcadas como no detectado."
            )

    _seccion("Resultados SOS — resumen")
    detectados  = df_sos["sos_fecha"].notna().sum()
    sin_detectar = df_sos["sos_fecha"].isna().sum()
    _info(f"Parcelas con SOS detectado: {detectados}  |  Sin detectar: {sin_detectar}")

    print(f"\n  {'id_parcela':>12}  {'sos_fecha':>12}  {'sos_valor':>10}  {'pos_fecha':>12}  {'amplitud':>10}  {'umbral':>8}")
    print(f"  {'─'*12}  {'─'*12}  {'─'*10}  {'─'*12}  {'─'*10}  {'─'*8}")
    import pandas as pd
    for _, row in df_sos.iterrows():
        sos_f = str(row["sos_fecha"].date()) if pd.notna(row["sos_fecha"]) else "—"
        pos_f = str(row["pos_fecha"].date()) if pd.notna(row["pos_fecha"]) else "—"
        sv    = f"{row['sos_valor']:.4f}" if pd.notna(row.get("sos_valor")) else "—"
        amp   = f"{row['amplitud']:.4f}"  if pd.notna(row.get("amplitud")) else "—"
        umb   = f"{row['umbral']:.4f}"    if pd.notna(row.get("umbral"))   else "—"
        print(f"  {str(row['id_parcela']):>12}  {sos_f:>12}  {sv:>10}  {pos_f:>12}  {amp:>10}  {umb:>8}")

    # Estadísticas rápidas de la distribución de SOS
    sos_validos = df_sos["sos_fecha"].dropna()
    if not sos_validos.empty:
        print()
        _info(f"SOS más temprano : {sos_validos.min().date()}")
        _info(f"SOS más tardío   : {sos_validos.max().date()}")
        mediana = sos_validos.sort_values().iloc[len(sos_validos) // 2]
        _info(f"SOS mediana      : {mediana.date()}")

    _pausar()


def _accion_ver_sos_bd() -> None:
    """Consulta y muestra los SOS registrados en produccion_acumulada_ciclo."""
    _seccion("SOS en produccion_acumulada_ciclo")
    try:
        import pandas as pd
        with closing(_get_conn()) as conn:
            df = pd.read_sql(
                """
                SELECT id_ciclo, id_parcela, temporada, sos, fecha_inicio, fecha_fin, rendimiento
                FROM produccion_acumulada_ciclo
                ORDER BY id_parcela, fecha_inicio
                LIMIT 100;
                """,
                conn,
            )
        if df.empty:
            _warn("No hay registros en produccion_acumulada_ciclo.")
        else:
            pd.set_option("display.max_columns", None)
            pd.set_option("display.width", 130)
            print()
            print(df.to_string(index=False))
            _info(f"{len(df)} fila(s).")
    except Exception as exc:
        _error(str(exc))
    _pausar()


def _accion_segmentar_ciclos() -> None:
    """
    Segmenta una serie EVI/LSWI multi-anual de una parcela en ciclos
    individuales usando segmentar_ciclos (detección de valles).
    """
    _seccion("Segmentación de ciclos en serie multi-anual (experimental)")

    disponibles = _listar_parcelas_disponibles()
    id_parcela = _pedir_parcela(disponibles)
    if id_parcela is None:
        _pausar(); return

    indice = _pedir("Índice a usar (EVI / LSWI)", "EVI").upper()
    if indice not in ("EVI", "LSWI"):
        indice = "EVI"

    dist_raw = _pedir("Distancia mínima entre valles (días)", "90")
    prom_raw = _pedir("Prominencia mínima del valle", "0.15")
    try:
        distancia_min = int(dist_raw)
        prominencia   = float(prom_raw)
    except ValueError:
        _error("Valores inválidos, usando valores por defecto (90, 0.15).")
        distancia_min, prominencia = 90, 0.15

    _info("Cargando índices desde BD…")
    try:
        from pipeline.ingesta import cargar_indices_desde_bd
        dfs_crudos = cargar_indices_desde_bd(ids_parcelas=[id_parcela])
    except ValueError as exc:
        _warn(str(exc)); _pausar(); return
    except Exception as exc:
        _error(str(exc)); _pausar(); return

    try:
        from pipeline.modulo_vpm import preprocesar_indices_vpm
        resultado_prep = preprocesar_indices_vpm(dfs_crudos)
    except Exception as exc:
        _error(f"Error Whittaker: {exc}"); _pausar(); return

    col = f"id_{id_parcela}"
    df_indice = resultado_prep[indice]
    if col not in df_indice.columns:
        _error(f"Columna '{col}' no encontrada."); _pausar(); return

    import pandas as pd
    serie = df_indice[col].dropna()

    from pipeline.modulo_fenologico import segmentar_ciclos
    ciclos_detectados = segmentar_ciclos(
        serie,
        distancia_min_dias=distancia_min,
        prominencia_min=prominencia,
    )

    _seccion("Ciclos detectados")
    _info(f"Parcela {id_parcela} — {len(ciclos_detectados)} segmento(s) detectado(s):")
    print()
    print(f"  {'#':>4}  {'Inicio':>12}  {'Fin':>12}  {'Duración (días)':>16}")
    print(f"  {'─'*4}  {'─'*12}  {'─'*12}  {'─'*16}")
    for i, (ini, fin) in enumerate(ciclos_detectados, 1):
        dur = (fin - ini).days
        print(f"  {i:>4}  {str(ini.date()):>12}  {str(fin.date()):>12}  {dur:>16}")

    _pausar()


# ══════════════════════════════════════════════════════════════════════════════
# SECCIÓN 7 — Worker Diario
# ══════════════════════════════════════════════════════════════════════════════

def _menu_worker() -> None:
    while True:
        _seccion("7 · Worker Diario")
        key = _menu({
            "configurar":    "Configurar worker",
            "estado":        "Estado del worker",
            "ejecutar":      "Ejecutar ahora",
            "simular":       "Simular con fecha pasada",
            "ver_log":       "Ver log del worker",
            "registrar":     "Registrar tarea en Windows Scheduler",
            "desregistrar":  "Desregistrar tarea del Windows Scheduler",
        })
        if key == "0":
            return
        elif key == "configurar":
            _accion_worker_configurar()
        elif key == "estado":
            _accion_worker_estado()
        elif key == "ejecutar":
            _accion_worker_ejecutar()
        elif key == "simular":
            _accion_worker_simular()
        elif key == "ver_log":
            _accion_worker_ver_log()
        elif key == "registrar":
            _accion_worker_registrar()
        elif key == "desregistrar":
            _accion_worker_desregistrar()


def _cargar_worker_modulo():
    """Importa pipeline.worker con manejo de error (Req 11.7)."""
    try:
        import pipeline.worker as worker_mod
        return worker_mod
    except Exception as exc:
        _error(f"No se pudo cargar pipeline/worker.py: {exc}")
        return None


def _accion_worker_configurar() -> None:
    """Req 11.2 — Muestra y permite modificar worker_config.json."""
    _seccion("Configurar worker")
    worker_mod = _cargar_worker_modulo()
    if worker_mod is None:
        _pausar(); return

    try:
        cfg = worker_mod.cargar_config()
    except ValueError as exc:
        _error(str(exc))
        _pausar(); return

    cfg_original = dict(cfg)

    _info("Configuración actual:")
    for k, v in cfg.items():
        print(f"    {k}: {v}")

    print()
    _info("Deja en blanco para mantener el valor actual.")

    # activo
    raw = _pedir("activo (true/false)", str(cfg.get("activo", False)).lower())
    cfg["activo"] = raw.lower() in ("true", "1", "s", "si", "yes")

    # hora_ejecucion
    while True:
        raw = _pedir("hora_ejecucion (HH:MM)", cfg.get("hora_ejecucion", "06:00"))
        parts = raw.split(":")
        if (
            len(parts) == 2
            and parts[0].isdigit() and parts[1].isdigit()
            and 0 <= int(parts[0]) <= 23
            and 0 <= int(parts[1]) <= 59
        ):
            cfg["hora_ejecucion"] = raw
            break
        _warn("Formato inválido. Usa HH:MM (ej: 06:00).")

    # ventana_busqueda_dias
    while True:
        raw = _pedir("ventana_busqueda_dias (1-30)", str(cfg.get("ventana_busqueda_dias", 7)))
        if raw.isdigit() and 1 <= int(raw) <= 30:
            cfg["ventana_busqueda_dias"] = int(raw)
            break
        _warn("Debe ser un entero entre 1 y 30.")

    # temporada_activa
    while True:
        raw = _pedir("temporada_activa (primera/postrera)", cfg.get("temporada_activa", "primera"))
        if raw in ("primera", "postrera"):
            cfg["temporada_activa"] = raw
            break
        _warn("Debe ser 'primera' o 'postrera'.")

    # factor_sos
    while True:
        raw = _pedir("factor_sos (0.0-1.0)", str(cfg.get("factor_sos", 0.2)))
        try:
            f = float(raw)
            if 0.0 <= f <= 1.0:
                cfg["factor_sos"] = f
                break
            _warn("Debe estar entre 0.0 y 1.0.")
        except ValueError:
            _warn(f"'{raw}' no es un número válido.")

    scheduler_cambio = (
        cfg.get("activo") != cfg_original.get("activo")
        or cfg.get("hora_ejecucion") != cfg_original.get("hora_ejecucion")
    )

    if scheduler_cambio:
        if cfg.get("activo"):
            _info("Sincronizando con Windows Task Scheduler…")
            ok, msg = worker_mod.sincronizar_scheduler_con_config(cfg)
            if not ok:
                _error(msg)
                _warn("No se guardó la configuración: el Scheduler no pudo actualizarse.")
                _pausar(); return
            _ok(msg)
        else:
            if worker_mod.esta_registrado_en_scheduler():
                _info("Desregistrando del Windows Task Scheduler…")
                ok, msg = worker_mod.desregistrar_de_scheduler()
                if not ok:
                    _error(msg)
                    _warn("No se guardó la configuración: no se pudo eliminar la tarea.")
                    _pausar(); return
                _ok(msg)

    try:
        worker_mod.guardar_config(cfg)
        _ok("Configuración guardada en worker_config.json.")
    except Exception as exc:
        _error(f"Error guardando configuración: {exc}")

    _pausar()


def _accion_worker_estado() -> None:
    """Req 11.3 — Muestra el estado actual del worker."""
    _seccion("Estado del worker")
    worker_mod = _cargar_worker_modulo()
    if worker_mod is None:
        _pausar(); return

    try:
        cfg = worker_mod.cargar_config()
    except ValueError as exc:
        _error(str(exc))
        _pausar(); return

    activo  = cfg.get("activo", False)
    hora    = cfg.get("hora_ejecucion", "06:00")
    ult_ej  = cfg.get("ultima_ejecucion") or "—"
    ult_ok  = cfg.get("ultima_ejecucion_exitosa") or "—"
    prox    = cfg.get("proxima_ejecucion") or "—"
    en_sched = worker_mod.esta_registrado_en_scheduler()

    print()
    print(f"  Estado activo          : {'✅ Activo' if activo else '❌ Inactivo'}")
    print(f"  Hora de ejecución      : {hora}")
    print(f"  Última ejecución       : {ult_ej}")
    print(f"  Última ejecución ok    : {ult_ok}")
    print(f"  Próxima ejecución      : {prox}")
    print(f"  En Windows Scheduler   : {'✅ Sí' if en_sched else '❌ No'}")
    _pausar()


def _accion_worker_ejecutar() -> None:
    """Req 11.4 — Ejecuta el worker con la fecha del sistema."""
    _seccion("Ejecutar worker ahora")
    worker_mod = _cargar_worker_modulo()
    if worker_mod is None:
        _pausar(); return

    _info("Ejecutando worker con la fecha del sistema…")
    try:
        resumen = worker_mod.ejecutar(fecha_hoy=None)
    except Exception as exc:
        _error(f"Error durante la ejecución: {exc}")
        _pausar(); return

    _ok("Ejecución completada.")
    print()
    print(f"  Ciclos procesados      : {resumen['ciclos_procesados']}")
    print(f"  Fechas ingestadas      : {resumen['fechas_ingestadas']}")
    print(f"  Predicciones generadas : {resumen['predicciones_generadas']}")
    print(f"  Duración               : {resumen['duracion_segundos']:.1f} s")
    if resumen["errores"]:
        _warn(f"Errores: {len(resumen['errores'])}")
        for e in resumen["errores"]:
            print(f"    • {e}")
    _pausar()


def _accion_worker_simular() -> None:
    """Req 11.5 / Req 12 — Simula la ejecución con una fecha pasada."""
    _seccion("Simular con fecha pasada")
    worker_mod = _cargar_worker_modulo()
    if worker_mod is None:
        _pausar(); return

    fecha_sim = _pedir_fecha("Fecha simulada (YYYY-MM-DD)", "2025-08-15")

    _info(f"Simulando ejecución con fecha={fecha_sim} (sin conexión a openEO)…")
    try:
        resumen = worker_mod.ejecutar(fecha_hoy=fecha_sim)
    except Exception as exc:
        _error(f"Error durante la simulación: {exc}")
        _pausar(); return

    _ok(f"Simulación completada para fecha={fecha_sim}.")
    print()
    print(f"  Ciclos procesados      : {resumen['ciclos_procesados']}")
    print(f"  Predicciones generadas : {resumen['predicciones_generadas']}")
    print(f"  Duración               : {resumen['duracion_segundos']:.1f} s")
    if resumen["errores"]:
        _warn(f"Errores: {len(resumen['errores'])}")
        for e in resumen["errores"]:
            print(f"    • {e}")
    _pausar()


def _accion_worker_registrar() -> None:
    """Registra el worker en Windows Task Scheduler."""
    _seccion("Registrar tarea en Windows Scheduler")
    worker_mod = _cargar_worker_modulo()
    if worker_mod is None:
        _pausar(); return

    if worker_mod.esta_registrado_en_scheduler():
        _warn("La tarea ya está registrada. Se actualizará la hora.")

    hora = _pedir("Hora de ejecución (HH:MM)", "06:00")
    ok, msg = worker_mod.registrar_en_scheduler(hora)
    if ok:
        _ok(msg)
        cfg = worker_mod.cargar_config()
        cfg["activo"] = True
        cfg["hora_ejecucion"] = hora
        worker_mod.guardar_config(cfg)
    else:
        _error(msg)
    _pausar()


def _accion_worker_desregistrar() -> None:
    """Desregistra el worker de Windows Task Scheduler."""
    _seccion("Desregistrar tarea del Windows Scheduler")
    worker_mod = _cargar_worker_modulo()
    if worker_mod is None:
        _pausar(); return

    if not worker_mod.esta_registrado_en_scheduler():
        _warn("No hay tarea registrada en el Scheduler.")
        _pausar(); return

    confirmar = _pedir("¿Desregistrar la tarea? (s/n)", "n")
    if confirmar.lower() != "s":
        _info("Cancelado.")
        _pausar(); return

    ok, msg = worker_mod.desregistrar_de_scheduler()
    if ok:
        _ok(msg)
        cfg = worker_mod.cargar_config()
        cfg["activo"] = False
        worker_mod.guardar_config(cfg)
    else:
        _error(msg)
    _pausar()


def _accion_worker_ver_log() -> None:
    """Req 11.6 — Muestra las últimas N líneas del log del día."""
    _seccion("Ver log del worker")
    from datetime import date as _date

    raw_n = _pedir("Número de líneas a mostrar (0 = ninguna)", "50")
    try:
        n_lines = int(raw_n)
    except ValueError:
        _warn("Valor inválido, usando 50.")
        n_lines = 50

    if n_lines == 0:
        _info("Se solicitaron 0 líneas. No se mostrará nada.")
        _pausar(); return

    log_path = ROOT / "logs" / f"worker_{_date.today().strftime('%Y-%m-%d')}.log"

    if not log_path.exists():
        _warn("No hay log de ejecución para la fecha actual.")
        _pausar(); return

    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        ultimas = lines[-n_lines:] if len(lines) >= n_lines else lines
        print()
        for l in ultimas:
            print(f"  {l}")
    except Exception as exc:
        _error(f"Error leyendo el log: {exc}")
    _pausar()


# ══════════════════════════════════════════════════════════════════════════════
# MENÚ PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

_MENU_PRINCIPAL = {
    "parcelas":    "Gestión de parcelas vigentes",
    "ingesta":     "Ingesta satelital y climática (openEO)",
    "prediccion":  "Motor de predicción de rendimiento",
    "fenologico":  "Módulo fenológico (SOS, ciclos)",
    "bd":          "Inspección de la base de datos SQLite",
    "diagnostico": "Diagnóstico del proyecto",
    "worker":      "Worker Diario (automatización)",
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
        elif key == "fenologico":
            _menu_fenologico()
        elif key == "bd":
            _menu_bd()
        elif key == "diagnostico":
            _menu_diagnostico()
        elif key == "worker":
            _menu_worker()


if __name__ == "__main__":
    main()
