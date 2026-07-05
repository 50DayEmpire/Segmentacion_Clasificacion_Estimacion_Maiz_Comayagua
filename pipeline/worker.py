# pipeline/worker.py — Worker Diario del Pipeline de Estimación de Rendimiento de Maíz
"""
Ejecuta diariamente el ciclo completo del pipeline de teledetección:
  1. Detección de ciclos activos
  2. Detección de nuevas adquisiciones Sentinel-2
  3. Ingesta de índices EVI/LSWI y clima AgERA5
  4. Preprocesamiento Whittaker-Eilers
  5. Detección y persistencia del SOS
  6. Verificación y ejecución de ventanas de predicción T1/T2/T3

Uso directo:
    python -m pipeline.worker                          # fecha hoy
    python -m pipeline.worker --fecha 2025-08-15       # modo simulación

Requisito: worker_config.json en la raíz del proyecto.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKER_CONFIG_PATH = ROOT / "worker_config.json"
LOGS_DIR           = ROOT / "logs"

WORKER_CONFIG_DEFAULTS: dict[str, Any] = {
    "activo":               False,
    "hora_ejecucion":       "06:00",
    "ventana_busqueda_dias": 7,
    "temporada_activa":     "primera",
    "factor_sos":           0.2,
    "ultima_ejecucion":     None,
    "ultima_ejecucion_exitosa": None,
    "proxima_ejecucion":    None,
}

TASK_NAME = "MaizComayaguaWorker"


# ══════════════════════════════════════════════════════════════════════════════
# Configuración del worker
# ══════════════════════════════════════════════════════════════════════════════

def cargar_config() -> dict[str, Any]:
    """Carga worker_config.json; crea defaults si no existe."""
    if not WORKER_CONFIG_PATH.exists():
        guardar_config(WORKER_CONFIG_DEFAULTS.copy())
        return WORKER_CONFIG_DEFAULTS.copy()

    texto = WORKER_CONFIG_PATH.read_text(encoding="utf-8").strip()
    if not texto:
        return {}

    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"worker_config.json contiene JSON inválido: {exc}. "
            "Corrige o elimina el archivo antes de continuar."
        ) from exc


def guardar_config(cfg: dict[str, Any]) -> None:
    """Persiste worker_config.json con sangría legible."""
    WORKER_CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _actualizar_campo_config(campo: str, valor: Any) -> None:
    cfg = cargar_config()
    cfg[campo] = valor
    guardar_config(cfg)


# ══════════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════════

def _crear_logger(fecha_hoy: date, simulacion: bool = False) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    sufijo   = "_sim" if simulacion else ""
    nombre   = f"worker_{fecha_hoy.strftime('%Y-%m-%d')}{sufijo}"
    log_path = LOGS_DIR / f"{nombre}.log"

    logger = logging.getLogger(nombre)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        fh = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    return logger


def _log_seguro(logger: logging.Logger, nivel: str, msg: str, *args) -> None:
    try:
        getattr(logger, nivel)(msg, *args)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Windows Task Scheduler
# ══════════════════════════════════════════════════════════════════════════════

def registrar_en_scheduler(hora_ejecucion: str) -> tuple[bool, str]:
    """Registra (o re-registra) el worker en Windows Task Scheduler."""
    python_exe = sys.executable
    worker_py  = str(ROOT / "pipeline" / "worker.py")
    cmd_crear  = [
        "schtasks.exe", "/Create", "/F",
        "/TN", TASK_NAME,
        "/TR", f'"{python_exe}" "{worker_py}"',
        "/SC", "DAILY",
        "/ST", hora_ejecucion,
        # "/RL", "HIGHEST",
    ]
    try:
        result = subprocess.run(cmd_crear, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, f"Tarea '{TASK_NAME}' registrada para las {hora_ejecucion}."
    except Exception as exc:
        return False, str(exc)


def desregistrar_de_scheduler() -> tuple[bool, str]:
    """Elimina el worker de Windows Task Scheduler."""
    cmd_del = ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"]
    try:
        result = subprocess.run(cmd_del, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, f"Tarea '{TASK_NAME}' eliminada del Scheduler."
    except Exception as exc:
        return False, str(exc)


def esta_registrado_en_scheduler() -> bool:
    try:
        result = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def sincronizar_scheduler_con_config(cfg: dict[str, Any]) -> tuple[bool, str]:
    """
    Alinea el Task Scheduler con ``activo`` y ``hora_ejecucion`` de la config.
    Retorna (éxito, mensaje). No modifica worker_config.json.
    """
    if not cfg.get("activo"):
        if esta_registrado_en_scheduler():
            return desregistrar_de_scheduler()
        return True, "Worker inactivo; no hay tarea en el Scheduler."

    hora = cfg.get("hora_ejecucion", "06:00")
    return registrar_en_scheduler(hora)


# ══════════════════════════════════════════════════════════════════════════════
# Orquestación por ciclo (delega en pipeline/)
# ══════════════════════════════════════════════════════════════════════════════

def _cargar_geojson_parcelas() -> dict:
    import geopandas as gpd
    from config import GPKG_PATH, LAYERS_GPKG

    gdf = gpd.read_file(str(GPKG_PATH), layer=LAYERS_GPKG["parcelas"]).to_crs("EPSG:4326")
    return json.loads(gdf.to_json())


def _conectar_openeo_cdse():
    import openeo
    from config import OPENEO
    return openeo.connect(f"https://{OPENEO}").authenticate_oidc()


def _conectar_openeo_fed():
    import openeo
    from config import OPENEOFED
    return openeo.connect(f"https://{OPENEOFED}").authenticate_oidc()


def _detectar_nuevas_adquisiciones(
    ciclo: dict,
    geojson: dict,
    fecha_hoy: date,
    ventana_busqueda_dias: int,
    logger: logging.Logger,
) -> list[date]:
    from pipeline.openeo_catalogo import (
        detectar_fechas_nuevas,
        obtener_fechas_disponibles_s2,
    )

    id_ciclo   = ciclo["id_ciclo"]
    id_parcela = ciclo["id_parcela"]
    fecha_ini  = (fecha_hoy - timedelta(days=ventana_busqueda_dias)).strftime("%Y-%m-%d")
    fecha_fin  = fecha_hoy.strftime("%Y-%m-%d")

    try:
        conn = _conectar_openeo_cdse()
        fechas_catalogo = obtener_fechas_disponibles_s2(
            conn, geojson, fecha_ini, fecha_fin,
        )
    except Exception as exc:
        _log_seguro(
            logger, "warning",
            "Error consultando catálogo Sentinel-2 para ciclo id_ciclo=%s: %s\n%s",
            id_ciclo, exc, traceback.format_exc(),
        )
        return []

    from contextlib import closing
    from utils.conexionDB import get_connection_raw
    sql = "SELECT DISTINCT fecha FROM series_diarias_vpm WHERE id_parcela = ? AND fecha BETWEEN ? AND ?"
    with closing(get_connection_raw()) as _conn:
        fechas_existentes = set(
            r[0] for r in _conn.execute(sql, (id_parcela, fecha_ini, fecha_fin)).fetchall()
        )

    nuevas = detectar_fechas_nuevas(fechas_catalogo, fechas_existentes)

    if not nuevas:
        _log_seguro(logger, "info", "Sin nuevas adquisiciones para ciclo id_ciclo=%s", id_ciclo)

    return nuevas


def _ingestar_fechas_nuevas(
    fechas_nuevas: list[date],
    geojson: dict,
    logger: logging.Logger,
    id_ciclo: int,
) -> tuple[int, int]:
    from pipeline.ingesta import obtener_clima, obtener_indices

    if not fechas_nuevas:
        return 0, 0

    fecha_ini = min(fechas_nuevas).strftime("%Y-%m-%d")
    fecha_fin = max(fechas_nuevas).strftime("%Y-%m-%d")
    n = len(fechas_nuevas)

    try:
        conn_cdse = _conectar_openeo_cdse()
        obtener_indices(conn_cdse, geojson, fecha_ini, fecha_fin)
    except Exception as exc:
        _log_seguro(
            logger, "error",
            "Error en ingesta de índices id_ciclo=%s: %s\n%s",
            id_ciclo, exc, traceback.format_exc(),
        )
        return 0, n

    try:
        conn_fed = _conectar_openeo_fed()
        obtener_clima(conn_fed, geojson, fecha_ini, fecha_fin)
    except Exception as exc:
        _log_seguro(
            logger, "warning",
            "Error en ingesta de clima id_ciclo=%s (se reintentará después): %s",
            id_ciclo, exc,
        )

    _log_seguro(
        logger, "info",
        "Ingesta ciclo id_ciclo=%s: %d fechas procesadas.",
        id_ciclo, n,
    )
    return n, 0


def _preprocesar_ciclo(
    ciclo: dict,
    fecha_hoy: date,
    logger: logging.Logger,
) -> dict[int, dict] | None:
    from pipeline.ingesta import cargar_indices_desde_bd
    from pipeline.modulo_vpm import preprocesar_indices_vpm, guardar_indices_suavizados

    id_ciclo   = ciclo["id_ciclo"]
    id_parcela = ciclo["id_parcela"]
    fecha_inicio = ciclo.get("fecha_inicio")
    lswi_max   = ciclo.get("lswi_max")

    try:
        dfs_crudos = cargar_indices_desde_bd(
            fecha_inicio=str(fecha_inicio) if fecha_inicio else None,
            fecha_fin=str(fecha_hoy),
            ids_parcelas=[id_parcela],
        )
    except ValueError as exc:
        _log_seguro(
            logger, "warning",
            "Sin datos en BD para id_ciclo=%s id_parcela=%s: %s",
            id_ciclo, id_parcela, exc,
        )
        return None
    except Exception as exc:
        _log_seguro(
            logger, "error",
            "Error cargando índices id_ciclo=%s: %s\n%s",
            id_ciclo, exc, traceback.format_exc(),
        )
        return None

    col = f"id_{id_parcela}"
    df_evi = dfs_crudos["EVI"]
    n_validos = int(df_evi[col].notna().sum()) if col in df_evi.columns else 0

    if n_validos < 3:
        _log_seguro(
            logger, "warning",
            "Serie insuficiente para suavizado: id_ciclo=%s, id_parcela=%s",
            id_ciclo, id_parcela,
        )
        return None

    lswi_max_kw = {col: float(lswi_max)} if lswi_max else None

    try:
        dfs_vpm = preprocesar_indices_vpm(dfs_crudos, lswi_max=lswi_max_kw)
    except Exception as exc:
        _log_seguro(
            logger, "error",
            "Error Whittaker id_ciclo=%s: %s\n%s",
            id_ciclo, exc, traceback.format_exc(),
        )
        return None

    try:
        guardar_indices_suavizados(id_ciclo, id_parcela, dfs_vpm)
    except Exception as exc:
        _log_seguro(
            logger, "error",
            "Error persistiendo índices suavizados id_ciclo=%s: %s\n%s",
            id_ciclo, exc, traceback.format_exc(),
        )

    return {id_parcela: dfs_vpm}


def _procesar_ciclo(
    ciclo: dict,
    fecha_hoy: date,
    ventana_busqueda_dias: int,
    factor_sos: float,
    simulacion: bool,
    logger: logging.Logger,
) -> tuple[int, int]:
    """Orquesta el procesamiento de un ciclo activo. Retorna (ingestadas, predicciones)."""
    from pipeline.modulo_fenologico import detectar_y_persistir_sos_ciclo
    from pipeline.modulo_predictivo import (
        ejecutar_prediccion_ventana,
        existe_prediccion_ventana,
        prediccion_congelada_antes_de,
    )

    id_ciclo = ciclo["id_ciclo"]
    _log_seguro(
        logger, "info",
        "── Procesando ciclo id_ciclo=%s temporada=%s ──",
        id_ciclo, ciclo.get("temporada"),
    )

    ingestadas = 0

    if not simulacion:
        try:
            geojson = _cargar_geojson_parcelas()
        except Exception as exc:
            _log_seguro(logger, "error", "Error cargando GeoJSON id_ciclo=%s: %s", id_ciclo, exc)
            geojson = None

        if geojson:
            fechas_nuevas = _detectar_nuevas_adquisiciones(
                ciclo, geojson, fecha_hoy, ventana_busqueda_dias, logger,
            )
            if fechas_nuevas:
                ok, _ = _ingestar_fechas_nuevas(fechas_nuevas, geojson, logger, id_ciclo)
                ingestadas = ok

    dfs_vpm_por_parcela = _preprocesar_ciclo(ciclo, fecha_hoy, logger)
    if dfs_vpm_por_parcela is None:
        return ingestadas, 0

    sos_previo = ciclo.get("sos")
    ciclo = detectar_y_persistir_sos_ciclo(ciclo, dfs_vpm_por_parcela, factor_sos)
    if sos_previo is None and ciclo.get("sos") is None:
        _log_seguro(logger, "info", "SOS no detectado aún para ciclo id_ciclo=%s", id_ciclo)
    elif sos_previo is None and ciclo.get("sos") is not None:
        _log_seguro(
            logger, "info",
            "SOS persistido id_ciclo=%s: sos=%s t1=%s t2=%s t3=%s",
            id_ciclo, ciclo.get("sos"), ciclo.get("t1"),
            ciclo.get("t2"), ciclo.get("t3"),
        )

    predicciones = 0
    for ventana in ("T1", "T2", "T3"):
        fecha_ventana_str = ciclo.get(ventana.lower())
        if fecha_ventana_str is None:
            continue

        fecha_ventana = date.fromisoformat(str(fecha_ventana_str))
        if fecha_hoy < fecha_ventana:
            continue

        if simulacion:
            if prediccion_congelada_antes_de(id_ciclo, ventana, fecha_hoy):
                _log_seguro(
                    logger, "info",
                    "Predicción ya congelada para ciclo id_ciclo=%s, ventana=%s. Se omite.",
                    id_ciclo, ventana,
                )
                continue
        elif existe_prediccion_ventana(id_ciclo, ventana):
            _log_seguro(
                logger, "info",
                "Predicción ya congelada para ciclo id_ciclo=%s, ventana=%s. Se omite.",
                id_ciclo, ventana,
            )
            continue

        # Punto de extensión: Clasificación Maíz/No-Maíz (Req 7)
        # Propósito    : Filtrar parcelas con maíz vs otro cultivo.
        # Datos entrada: indices_suavizados, perfil fenológico (SOS, t1, t2, t3).
        # Tabla destino: clasificacion_parcelas (por crear).
        # Llamada futura: pipeline.modulo_clasificacion.clasificar(ciclo)
        pass

        try:
            resultado = ejecutar_prediccion_ventana(
                ciclo, ventana, fecha_ventana,
                dfs_vpm_por_parcela, fecha_hoy,
            )
        except RuntimeError as exc:
            _log_seguro(logger, "error", "%s", exc)
            continue
        except Exception as exc:
            _log_seguro(
                logger, "error",
                "Fallo flujo VPM ventana %s id_ciclo=%s: %s\n%s",
                ventana, id_ciclo, exc, traceback.format_exc(),
            )
            continue

        if resultado and resultado.get("parcelas_ok"):
            predicciones += resultado["parcelas_ok"]
            _log_seguro(
                logger, "info",
                "Ventana %s ejecutada id_ciclo=%s: 1 parcela, rendimiento=%.2f qq/ha, "
                "congelamiento=%s",
                ventana, id_ciclo, resultado["yield_qq_ha"],
                resultado["fecha_congelamiento"],
            )

    return ingestadas, predicciones


def _finalizar_ejecucion(
    logger: logging.Logger,
    ts_inicio: datetime,
    ciclos: int,
    ingestadas: int,
    predicciones: int,
) -> None:
    duracion = (datetime.utcnow() - ts_inicio).total_seconds()
    _log_seguro(
        logger, "info",
        "=== FIN WORKER === ciclos=%d | fechas_ingestadas=%d | "
        "predicciones=%d | duracion=%.1fs",
        ciclos, ingestadas, predicciones, duracion,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Punto de entrada principal
# ══════════════════════════════════════════════════════════════════════════════

def ejecutar(fecha_hoy: date | str | None = None) -> dict:
    """
    Punto de entrada principal del worker.

    Parámetros
    ----------
    fecha_hoy : date | str | None
        Si se provee, activa modo simulación con esa fecha como referencia.

    Retorna
    -------
    dict con ciclos_procesados, fechas_ingestadas, predicciones_generadas,
    duracion_segundos y errores.
    """
    from pipeline.flujos_trabajo import obtener_ciclos_activos

    simulacion = fecha_hoy is not None
    if simulacion:
        if isinstance(fecha_hoy, str):
            fecha_hoy = date.fromisoformat(fecha_hoy)
    else:
        fecha_hoy = date.today()

    logger = _crear_logger(fecha_hoy, simulacion=simulacion)

    try:
        cfg = cargar_config()
    except ValueError as exc:
        _log_seguro(logger, "error", "JSON inválido en worker_config.json: %s", exc)
        raise

    ts_inicio = datetime.utcnow()
    try:
        _actualizar_campo_config("ultima_ejecucion", ts_inicio.isoformat())
    except Exception:
        pass

    temporada_activa      = cfg.get("temporada_activa", "primera")
    ventana_busqueda_dias = int(cfg.get("ventana_busqueda_dias", 7))
    factor_sos            = float(cfg.get("factor_sos", 0.2))

    if simulacion:
        _log_seguro(logger, "info", "MODO SIMULACIÓN — Fecha simulada: %s", fecha_hoy)
    _log_seguro(
        logger, "info",
        "=== INICIO WORKER === fecha=%s | temporada=%s | factor_sos=%.2f | "
        "ventana_busqueda=%dd | simulacion=%s",
        fecha_hoy, temporada_activa, factor_sos,
        ventana_busqueda_dias, simulacion,
    )
    _log_seguro(logger, "info", "Configuración activa: %s", json.dumps(cfg, default=str))

    ciclos_procesados  = 0
    total_ingestadas   = 0
    total_predicciones = 0
    errores: list[str] = []

    ciclos_activos = obtener_ciclos_activos(temporada_activa, fecha_hoy)
    _log_seguro(logger, "info", "Ciclos activos encontrados: %d", len(ciclos_activos))

    if not ciclos_activos:
        _log_seguro(
            logger, "info",
            "No se encontraron ciclos activos para la temporada configurada: %s",
            temporada_activa,
        )
        _finalizar_ejecucion(logger, ts_inicio, 0, 0, 0)
        try:
            _actualizar_campo_config(
                "ultima_ejecucion_exitosa", datetime.utcnow().isoformat(),
            )
        except Exception:
            pass
        return {
            "ciclos_procesados": 0,
            "fechas_ingestadas": 0,
            "predicciones_generadas": 0,
            "duracion_segundos": (datetime.utcnow() - ts_inicio).total_seconds(),
            "errores": [],
        }

    for ciclo in ciclos_activos:
        id_ciclo = ciclo["id_ciclo"]
        try:
            ingestadas, predicciones = _procesar_ciclo(
                ciclo=ciclo,
                fecha_hoy=fecha_hoy,
                ventana_busqueda_dias=ventana_busqueda_dias,
                factor_sos=factor_sos,
                simulacion=simulacion,
                logger=logger,
            )
            total_ingestadas   += ingestadas
            total_predicciones += predicciones
            ciclos_procesados  += 1
        except Exception as exc:
            msg = f"Error inesperado en ciclo id_ciclo={id_ciclo}: {exc}"
            errores.append(msg)
            _log_seguro(logger, "error", "%s\n%s", msg, traceback.format_exc())

    duracion = (datetime.utcnow() - ts_inicio).total_seconds()
    _finalizar_ejecucion(
        logger, ts_inicio, ciclos_procesados, total_ingestadas, total_predicciones,
    )

    try:
        _actualizar_campo_config(
            "ultima_ejecucion_exitosa", datetime.utcnow().isoformat(),
        )
    except Exception:
        pass

    return {
        "ciclos_procesados":      ciclos_procesados,
        "fechas_ingestadas":      total_ingestadas,
        "predicciones_generadas": total_predicciones,
        "duracion_segundos":      duracion,
        "errores":                errores,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Worker diario — pipeline maíz Comayagua")
    parser.add_argument(
        "--fecha",
        metavar="YYYY-MM-DD",
        default=None,
        help="Fecha simulada (modo simulación). Si se omite, usa la fecha del sistema.",
    )
    args = parser.parse_args()

    resumen = ejecutar(fecha_hoy=args.fecha)
    print("\n── Resumen de ejecución ──────────────────────────")
    for k, v in resumen.items():
        print(f"  {k}: {v}")
