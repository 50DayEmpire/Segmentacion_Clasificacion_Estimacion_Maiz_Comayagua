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
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKER_CONFIG_PATH = ROOT / "worker_config.json"
LOGS_DIR           = ROOT / "logs"

WORKER_CONFIG_DEFAULTS: dict[str, Any] = {
    "activo":               False,
    "hora_ejecucion":       "06:00",
    "temporada_activa":     "primera",
    "factor_sos":           0.2,
    "clasificar":           True,
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


def _calcular_proxima_ejecucion(cfg: dict[str, Any]) -> str:
    """
    Calcula la próxima fecha/hora de ejecución.

    Si la hora de ejecución de hoy ya pasó, retorna mañana a esa hora;
    si no, retorna hoy a esa hora.
    """
    hora = cfg.get("hora_ejecucion", "06:00")
    try:
        h, m = hora.split(":")
        ahora = datetime.now()
        hoy_ejec = datetime(ahora.year, ahora.month, ahora.day, int(h), int(m))
        if ahora < hoy_ejec:
            proxima = hoy_ejec
        else:
            proxima = hoy_ejec + timedelta(days=1)
        return proxima.isoformat()
    except (ValueError, IndexError):
        return (datetime.now() + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        ).isoformat()


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
    from utils.conexionDB import get_db_path

    gdf = gpd.read_file(str(get_db_path()), layer=LAYERS_GPKG["parcelas"]).to_crs("EPSG:4326")
    return json.loads(gdf.to_json())


def _conectar_openeo_cdse():
    import openeo
    from config import OPENEO
    return openeo.connect(f"https://{OPENEO}").authenticate_oidc()


def _conectar_openeo_fed():
    import openeo
    from config import OPENEOFED
    return openeo.connect(f"https://{OPENEOFED}").authenticate_oidc()


def _calcular_ventana_ingesta(
    id_parcela: int,
    fecha_hoy: date,
    dias_fallback_sin_historial: int,
) -> tuple[str, str]:
    """
    Ventana dinámica de ingesta S2: desde el día siguiente a la última fecha
    registrada en series_diarias_vpm para la parcela, hasta hoy. Garantiza
    ingestar siempre al menos la(s) adquisición(es) más reciente(s) sin
    reconsultar lo ya almacenado.

    Si no hay historial (caso atípico para un ciclo ya activo, pero posible
    si series_diarias_vpm se limpió o el ciclo es recién promovido), cae a
    ``dias_fallback_sin_historial`` como red de seguridad.
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    with closing(get_connection_raw()) as conn:
        row = conn.execute(
            "SELECT MAX(fecha) FROM series_diarias_vpm WHERE id_parcela = ?",
            (id_parcela,),
        ).fetchone()

    ultima_fecha = row[0] if row and row[0] else None

    if ultima_fecha:
        fecha_ini = date.fromisoformat(str(ultima_fecha)) + timedelta(days=1)
    else:
        fecha_ini = fecha_hoy - timedelta(days=dias_fallback_sin_historial)

    if fecha_ini > fecha_hoy:
        fecha_ini = fecha_hoy  # ya al día; ventana mínima de 1 día

    return fecha_ini.strftime("%Y-%m-%d"), fecha_hoy.strftime("%Y-%m-%d")





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
    factor_sos: float,
    simulacion: bool,
    logger: logging.Logger,
    clasificar: bool = True,
) -> tuple[int, int]:
    """Orquesta el procesamiento de un ciclo activo. Retorna (ingestadas, predicciones)."""
    from contextlib import closing
    from pipeline.modulo_fenologico import detectar_y_persistir_sos_ciclo
    from pipeline.modulo_predictivo import (
        ejecutar_prediccion_ventana,
        existe_prediccion_ventana,
        prediccion_congelada_antes_de,
    )
    from utils.conexionDB import get_connection_raw

    id_ciclo = ciclo["id_ciclo"]
    _log_seguro(
        logger, "info",
        "── Procesando ciclo id_ciclo=%s temporada=%s ──",
        id_ciclo, ciclo.get("temporada"),
    )

    ingestadas = 0
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
    for ventana in ("T1", "T2", "T3", "EOS"):
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

        # La clasificación fenológica se ejecuta dentro de
        # ejecutar_prediccion_ventana() en modulo_predictivo.py

        try:
            resultado = ejecutar_prediccion_ventana(
                ciclo, ventana, fecha_ventana,
                dfs_vpm_por_parcela, fecha_hoy,
                clasificar=clasificar,
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
            if ventana == "EOS":
                with closing(get_connection_raw()) as conn:
                    with conn:
                        conn.execute("""
                            UPDATE produccion_acumulada_ciclo
                            SET rendimiento = ?, produccion_total = ?
                            WHERE id_ciclo = ?
                        """, (
                            resultado.get("yield_qq_ha"),
                            resultado.get("yield_qq_parcela"),
                            id_ciclo,
                        ))

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
# Detección de ciclos nuevos y promoción candidato → activo
# ══════════════════════════════════════════════════════════════════════════════

CONSECUTIVOS_REQUERIDOS = 3
LIMITE_MAX_DIAS_HISTORIAL_CANDIDATO   = 180


def _obtener_ultimo_eos(id_parcela: int) -> str:
    from contextlib import closing
    from utils.conexionDB import get_connection_raw
    with closing(get_connection_raw()) as conn:
        row = conn.execute(
            """SELECT MAX(eos) FROM produccion_acumulada_ciclo
               WHERE id_parcela = ? AND estado_ciclo = 'finalizado' AND eos IS NOT NULL""",
            (id_parcela,),
        ).fetchone()
    ultimo_eos = row[0] if row and row[0] else None
    if ultimo_eos is None:
        raise RuntimeError(
            f"id_parcela={id_parcela} no tiene ningún ciclo finalizado. "
            "Ejecuta el seed histórico offline antes de usar el worker."
        )
    return str(ultimo_eos)


def _calcular_ventana_busqueda_candidato(
    id_parcela: int,
    fecha_hoy: date,
) -> tuple[str, str, str]:
    ultimo_eos = _obtener_ultimo_eos(id_parcela)
    fecha_ini = date.fromisoformat(ultimo_eos) + timedelta(days=1)
    limite_min = fecha_hoy - timedelta(days=LIMITE_MAX_DIAS_HISTORIAL_CANDIDATO)
    if fecha_ini < limite_min:
        fecha_ini = limite_min
    if fecha_ini > fecha_hoy:
        fecha_ini = fecha_hoy
    return ultimo_eos, fecha_ini.strftime("%Y-%m-%d"), fecha_hoy.strftime("%Y-%m-%d")

def _calcular_fechas_ciclo(sos_date: date) -> dict[str, date | None]:
    from config import DIAS_VENTANAS
    return {
        "t1":  sos_date + timedelta(days=DIAS_VENTANAS["T1"]),
        "t2":  sos_date + timedelta(days=DIAS_VENTANAS["T2"]),
        "t3":  sos_date + timedelta(days=DIAS_VENTANAS["T3"]),
        "eos": None,  # ciclo no ha terminado; se define en seed offline
    }


def _temporada_por_sos(sos_date: date) -> str:
    return "primera" if 4 <= sos_date.month <= 7 else "postrera"


def detectar_y_crear_ciclos_pendientes(
    fecha_hoy: date,
    factor_sos: float,
    simulacion: bool,
    logger: logging.Logger,
) -> tuple[int, int]:
    """
    1. Identifica parcelas sin ciclo activo/candidato (de cualquier temporada).
    2. Para cada parcela pendiente: carga EVI desde BD, corre SOS candidato
       y crea un registro en ``produccion_acumulada_ciclo`` con
       ``estado_ciclo = 'candidato'`` y temporada determinada por el mes del SOS.
    3. Para cada candidato existente: verifica que la señal persista N
       observaciones válidas consecutivas por encima del umbral; si cumple,
       lo promueve a ``estado_ciclo = 'activo'``.

    Retorna (candidatos_creados, candidatos_promovidos).
    """
    from contextlib import closing
    from utils.conexionDB import get_connection_raw

    # 1. Obtener parcelas vigentes
    with closing(get_connection_raw()) as conn:
        parcelas = [r[0] for r in conn.execute("SELECT id_parcela FROM parcelas_vigentes").fetchall()]

    if not parcelas:
        return 0, 0

    # 2. Obtener ciclos existentes (candidato + activo) de cualquier temporada
    placeholders = ",".join(["?" for _ in parcelas])
    sql_existentes = f"""
        SELECT id_parcela, estado_ciclo, id_ciclo
        FROM produccion_acumulada_ciclo
        WHERE id_parcela IN ({placeholders})
          AND (estado_ciclo IN ('candidato', 'activo')
               OR (estado_ciclo IS NULL AND eos IS NULL))
    """
    with closing(get_connection_raw()) as conn:
        existentes = conn.execute(sql_existentes, tuple(parcelas)).fetchall()

    parcelas_con_ciclo = {r[0] for r in existentes}
    parcelas_candidato = {r[0] for r in existentes if r[1] == 'candidato'}

    pendientes = [p for p in parcelas if p not in parcelas_con_ciclo]
    if not pendientes and not parcelas_candidato:
        _log_seguro(logger, "info", "Todas las parcelas ya tienen ciclo activo o candidato.")
        return 0, 0

    _log_seguro(
        logger, "info",
        "Ciclos pendientes: %d | Candidatos a promover: %d",
        len(pendientes), len(parcelas_candidato),
    )

    creados = 0
    promovidos = 0

    # 3. Crear candidatos para parcelas pendientes
    if pendientes and not simulacion:
        geojson = _cargar_geojson_parcelas()
        if not geojson:
            _log_seguro(logger, "error", "No se pudo cargar GeoJSON para detección de ciclos.")

        # Una sola lectura batch desde BD (la ingesta STAC ya corrió antes)
        prev_eos_por_parcela: dict[int, str] = {}
        fechas_por_parcela: dict[int, tuple[str, str]] = {}
        fecha_min = fecha_max = None
        for id_parcela in pendientes:
            prev_eos, ini, fin = _calcular_ventana_busqueda_candidato(id_parcela, fecha_hoy)
            prev_eos_por_parcela[id_parcela] = prev_eos
            fechas_por_parcela[id_parcela] = (ini, fin)
            if fecha_min is None or ini < fecha_min:
                fecha_min = ini
            if fecha_max is None or fin > fecha_max:
                fecha_max = fin

        from pipeline.ingesta import cargar_indices_desde_bd
        try:
            dfs = cargar_indices_desde_bd(
                fecha_inicio=fecha_min, fecha_fin=fecha_max,
                ids_parcelas=pendientes,
            )
        except ValueError:
            _log_seguro(
                logger, "warning",
                "Sin datos en BD para parcelas pendientes en rango %s–%s. "
                "No se crearán candidatos esta ejecución.",
                fecha_min, fecha_max,
            )
            pendientes = []
            dfs = None

        if dfs is not None:
            from pipeline.modulo_vpm import preprocesar_indices_vpm
            dfs_vpm = preprocesar_indices_vpm(dfs)

        for id_parcela in pendientes:
            try:
                fecha_ini_busq, fecha_fin_busq = fechas_por_parcela[id_parcela]
                col = f"id_{id_parcela}"
                df_evi = dfs_vpm["EVI"]
                if col not in df_evi.columns:
                    _log_seguro(
                        logger, "info",
                        "Parcela %s: sin columna EVI en BD, se omite.",
                        id_parcela,
                    )
                    continue

                serie = df_evi[col].dropna()
                if len(serie) < 3:
                    _log_seguro(
                        logger, "info",
                        "Parcela %s: solo %d observaciones EVI (mínimo 3), se omite.",
                        id_parcela, len(serie),
                    )
                    continue

                from pipeline.modulo_fenologico import detectar_sos
                resultado = detectar_sos(
                    serie=serie.values,
                    fechas=serie.index,
                    factor=factor_sos,
                    ventana_busqueda=(fecha_ini_busq, fecha_fin_busq),
                )

                sos_fecha = resultado.get("sos_fecha")
                if sos_fecha is None:
                    continue

                sos_date = sos_fecha.date() if hasattr(sos_fecha, "date") else sos_fecha
                fechas_ciclo = _calcular_fechas_ciclo(sos_date)
                eos_date = fechas_ciclo["eos"]
                temporada_asignada = _temporada_por_sos(sos_date)

                from config import DIAS_VENTANAS as _DV
                eos_para_overlap = (
                    eos_date if eos_date is not None
                    else sos_date + timedelta(days=_DV["EOS"])
                )
                sql_overlap = """
                    SELECT COUNT(*) FROM produccion_acumulada_ciclo
                    WHERE id_parcela = ?
                      AND estado_ciclo IN ('candidato', 'activo')
                      AND sos IS NOT NULL
                      AND NOT (
                          COALESCE(eos, date(sos, '+120 days')) < ?
                          OR sos > ?
                      )
                """
                with closing(get_connection_raw()) as conn:
                    overlap = conn.execute(
                        sql_overlap,
                        (id_parcela, str(sos_date), str(eos_para_overlap)),
                    ).fetchone()[0]
                if overlap > 0:
                    _log_seguro(
                        logger, "warning",
                        "Solapamiento detectado para id_parcela=%s (sos=%s). "
                        "No se crea candidato.",
                        id_parcela, sos_date,
                    )
                    continue

                from pipeline.ingesta import cargar_indices_desde_bd
                try:
                    dfs_completo = cargar_indices_desde_bd(ids_parcelas=[id_parcela])
                    df_lswi = dfs_completo["LSWI"]
                    lswi_max_val = (
                        float(df_lswi[col].max())
                        if col in df_lswi.columns and not df_lswi[col].isna().all()
                        else None
                    )
                except ValueError:
                    lswi_max_val = None

                sql_insert = """
                    INSERT INTO produccion_acumulada_ciclo
                        (id_parcela, temporada, lswi_max, sos, t1, t2, t3, eos,
                         fecha_inicio, fecha_fin, estado_ciclo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidato')
                """
                with closing(get_connection_raw()) as conn:
                    with conn:
                        cursor = conn.execute(sql_insert, (
                            id_parcela, temporada_asignada, lswi_max_val, str(sos_date),
                            str(fechas_ciclo["t1"]), str(fechas_ciclo["t2"]),
                            str(fechas_ciclo["t3"]),
                            fechas_ciclo["eos"],  # None → ciclo no terminado
                            prev_eos_por_parcela[id_parcela],
                            fechas_ciclo["eos"],  # None → fecha_fin también
                        ))
                        nuevo_id_ciclo = cursor.lastrowid

                creados += 1
                _log_seguro(
                    logger, "info",
                    "Candidato creado: id_parcela=%s temporada=%s sos=%s eos=%s id_ciclo=%s",
                    id_parcela, temporada_asignada, sos_date, fechas_ciclo["eos"], nuevo_id_ciclo,
                )

            except Exception as exc:
                _log_seguro(
                    logger, "error",
                    "Error creando candidato id_parcela=%s: %s", id_parcela, exc,
                )
                continue

    # 4. Promover candidatos a activos si la señal persiste
    if not simulacion:
        import pandas as pd  # requerido por pd.Timestamp más abajo

        for id_parcela in parcelas_candidato:
            try:
                with closing(get_connection_raw()) as conn:
                    cand = conn.execute(
                        """SELECT id_ciclo, sos
                           FROM produccion_acumulada_ciclo
                           WHERE id_parcela = ? AND estado_ciclo = 'candidato'
                           ORDER BY id_ciclo LIMIT 1""",
                        (id_parcela,),
                    ).fetchone()

                if cand is None:
                    continue

                id_ciclo_cand, sos_cand_db = cand

                from pipeline.ingesta import cargar_indices_desde_bd
                try:
                    dfs = cargar_indices_desde_bd(
                        fecha_inicio=str(sos_cand_db) if sos_cand_db else None,
                        fecha_fin=str(fecha_hoy),
                        ids_parcelas=[id_parcela],
                    )
                except ValueError:
                    continue

                col = f"id_{id_parcela}"
                df_evi = dfs["EVI"]
                if col not in df_evi.columns:
                    continue

                serie = df_evi[col].dropna()
                if sos_cand_db:
                    serie = serie.loc[serie.index >= pd.Timestamp(sos_cand_db)]

                if len(serie) < 3:
                    continue

                s = serie.sort_index()
                pos_idx = s.idxmax()
                slope_izq = s.loc[s.index <= pos_idx] if not s.empty else s
                if slope_izq.empty:
                    continue

                base_valor = slope_izq.min()
                pos_valor = s.max()
                amplitud = pos_valor - base_valor
                if amplitud <= 0:
                    continue

                umbral = base_valor + factor_sos * amplitud

                racha_max = 0
                racha_actual = 0
                for val in (s >= umbral):
                    if val:
                        racha_actual += 1
                        racha_max = max(racha_max, racha_actual)
                    else:
                        racha_actual = 0

                if racha_max >= CONSECUTIVOS_REQUERIDOS:
                    with closing(get_connection_raw()) as conn:
                        with conn:
                            conn.execute(
                                """UPDATE produccion_acumulada_ciclo
                                   SET estado_ciclo = 'activo'
                                   WHERE id_ciclo = ? AND estado_ciclo = 'candidato'""",
                                (id_ciclo_cand,),
                            )
                    promovidos += 1
                    _log_seguro(
                        logger, "info",
                        "Candidato promovido a activo: id_parcela=%s id_ciclo=%s sos=%s racha=%d",
                        id_parcela, id_ciclo_cand, sos_cand_db, racha_max,
                    )
                else:
                    _log_seguro(
                        logger, "debug",
                        "Candidato id_parcela=%s aún no cumple racha: %d/%d",
                        id_parcela, racha_max, CONSECUTIVOS_REQUERIDOS,
                    )

            except Exception as exc:
                _log_seguro(
                    logger, "error",
                    "Error promoviendo candidato id_parcela=%s: %s", id_parcela, exc,
                )
                continue

    return creados, promovidos


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
    except Exception as exc:
        _log_seguro(logger, "warning", "No se pudo actualizar config: %s", exc)
        pass

    temporada_activa      = cfg.get("temporada_activa", "primera")
    factor_sos = float(cfg.get("factor_sos", 0.2))
    clasificar = cfg.get("clasificar", True)

    if simulacion:
        _log_seguro(logger, "info", "MODO SIMULACIÓN — Fecha simulada: %s", fecha_hoy)
    _log_seguro(
        logger, "info",
        "=== INICIO WORKER === fecha=%s | temporada=%s | factor_sos=%.2f | simulacion=%s",
        fecha_hoy, temporada_activa, factor_sos, simulacion,
    )
    _log_seguro(logger, "info", "Configuración activa: %s", json.dumps(cfg, default=str))
    from utils.conexionDB import get_db_path
    _log_seguro(logger, "info", "Base de datos: %s", get_db_path())

    ciclos_procesados  = 0
    total_ingestadas   = 0
    total_predicciones = 0
    errores: list[str] = []

    # ── Ingesta global de índices (STAC + consultado) ────────────────────────
    if not simulacion:
        try:
            geojson = _cargar_geojson_parcelas()
            conn_cdse = _conectar_openeo_cdse()
            from pipeline.ingesta import obtener_indices
            from config import ANIO_INICIAL_HISTORICO
            obtener_indices(
                conn_cdse, geojson,
                f"{ANIO_INICIAL_HISTORICO}-01-01", str(fecha_hoy),
            )
        except Exception as exc:
            _log_seguro(
                logger, "error",
                "Error en ingesta global de índices: %s\n%s",
                exc, traceback.format_exc(),
            )

        # ── Ingesta climática AgERA5 (backend federado) ──────────────────────
        try:
            conn_fed = _conectar_openeo_fed()
            from pipeline.ingesta import obtener_clima
            obtener_clima(
                conn_fed, geojson,
                f"{ANIO_INICIAL_HISTORICO}-01-01", str(fecha_hoy),
            )
        except Exception as exc:
            _log_seguro(
                logger, "error",
                "Error en ingesta climática: %s\n%s",
                exc, traceback.format_exc(),
            )

    # ── Detectar parcelas sin ciclo y promover candidatos ────────────────────
    try:
        creados, promovidos = detectar_y_crear_ciclos_pendientes(
            fecha_hoy=fecha_hoy,
            factor_sos=factor_sos,
            simulacion=simulacion,
            logger=logger,
        )
        if creados or promovidos:
            _log_seguro(
                logger, "info",
                "Ciclos: %d candidatos creados, %d promovidos a activo.",
                creados, promovidos,
            )
    except Exception as exc:
        _log_seguro(logger, "error", "Error en detección de ciclos pendientes: %s", exc)

    # ── Obtener ciclos activos (incluye los recién promovidos) ───────────────
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
            _actualizar_campo_config(
                "proxima_ejecucion", _calcular_proxima_ejecucion(cfg),
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
                factor_sos=factor_sos,
                simulacion=simulacion,
                logger=logger,
                clasificar=clasificar,
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
    from datetime import timezone
    try:
        _actualizar_campo_config(
            "ultima_ejecucion_exitosa", datetime.now(timezone.utc).isoformat(),
        )
        _actualizar_campo_config(
            "proxima_ejecucion", _calcular_proxima_ejecucion(cfg),
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
