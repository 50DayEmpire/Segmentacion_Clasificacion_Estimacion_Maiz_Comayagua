from __future__ import annotations

from datetime import date
from contextlib import closing

import pandas as pd

from config import ANIO_INICIAL_HISTORICO
from utils.conexionDB import get_connection_raw


def _rango_completo() -> tuple[date, date]:
    hoy = date.today()
    inicio = date(ANIO_INICIAL_HISTORICO, 1, 1)
    return inicio, hoy


def _fechas_stac() -> list[date]:
    """Retorna todas las fechas en ``cobertura_sentinel2`` ordenadas."""
    sql = "SELECT fecha FROM cobertura_sentinel2 ORDER BY fecha"
    with closing(get_connection_raw()) as conn:
        rows = conn.execute(sql).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def _fechas_bd_consultadas(
    fecha_inicio: str,
    fecha_fin: str,
) -> pd.DatetimeIndex:
    """
    Retorna las fechas que ya fueron consultadas a openEO para índices
    (column ``consultado = 1`` en ``series_diarias_vpm``).
    """
    sql = """
        SELECT DISTINCT fecha
        FROM series_diarias_vpm
        WHERE consultado = 1
          AND fecha BETWEEN ? AND ?
        ORDER BY fecha
    """
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(fecha_inicio, fecha_fin), parse_dates=["fecha"])
    if df.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(df["fecha"].dt.floor("D").unique())


def _fechas_bd_clima(
    fecha_inicio: str,
    fecha_fin: str,
) -> pd.DatetimeIndex:
    sql = """
        SELECT DISTINCT fecha
        FROM series_diarias_vpm
        WHERE fecha BETWEEN ? AND ?
          AND (temperatura_diaria_promedio IS NOT NULL
               OR radiacion_total_promedio IS NOT NULL)
        ORDER BY fecha
    """
    with closing(get_connection_raw()) as conn:
        df = pd.read_sql(sql, conn, params=(fecha_inicio, fecha_fin), parse_dates=["fecha"])
    if df.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(df["fecha"].dt.floor("D").unique())


def _compactar_gaps(faltantes: pd.DatetimeIndex) -> list[tuple[str, str]]:
    if faltantes.empty:
        return []
    gaps: list[tuple[str, str]] = []
    i = 0
    n = len(faltantes)
    while i < n:
        g_ini = faltantes[i]
        g_fin = g_ini
        j = i + 1
        while j < n and (faltantes[j] - faltantes[j - 1]).days == 1:
            g_fin = faltantes[j]
            j += 1
        gaps.append((str(g_ini.date()), str(g_fin.date())))
        i = j
    return gaps


def reporte_cobertura() -> dict:
    """
    Genera un reporte de cobertura comparando las fechas de adquisición
    reales de Sentinel-2 (vía STAC, tabla ``cobertura_sentinel2``) contra
    las fechas consultadas a openEO (``consultado = 1``).

    Para clima (AgERA5) asume cobertura total por ser un reanálisis.

    Retorna
    -------
    dict con secciones ``indices``, ``clima`` y ``resumen``.
    """
    inicio, hoy = _rango_completo()
    inicio_str = inicio.isoformat()
    hoy_str = hoy.isoformat()

    print(f"\n  Rango histórico: [{inicio_str} → {hoy_str}]")

    # ── 1. Índices Sentinel-2 ─────────────────────────────────────────────
    fechas_stac = _fechas_stac()
    if fechas_stac:
        print(f"\n  [SAT] Cobertura STAC: {len(fechas_stac)} fecha(s) "
              f"[{fechas_stac[0]} → {fechas_stac[-1]}]")
        stac_desde = str(fechas_stac[0])
        stac_hasta = str(fechas_stac[-1])
    else:
        print(f"\n  [SAT] Tabla ``cobertura_sentinel2`` vacía. "
              f"Ejecuta ``actualizar_cobertura()`` primero.")
        stac_desde = None
        stac_hasta = None

    fechas_bd = _fechas_bd_consultadas(inicio_str, hoy_str)
    n_bd = len(fechas_bd)

    if not fechas_bd.empty:
        print(f"  BD consultadas (consultado=1): {n_bd} fecha(s) "
              f"[{fechas_bd.min().date()} → {fechas_bd.max().date()}]")
    else:
        print(f"  BD consultadas (consultado=1): 0 fechas")

    # Gaps contra STAC
    if fechas_stac and n_bd > 0:
        stac_idx = pd.DatetimeIndex(fechas_stac)
        faltantes = stac_idx.difference(fechas_bd, sort=False)
        n_faltantes = len(faltantes)
        pct_cubierto = round(100 * (len(stac_idx) - n_faltantes) / len(stac_idx), 1) if len(stac_idx) > 0 else 0.0
    elif fechas_stac:
        faltantes = pd.DatetimeIndex(fechas_stac)
        n_faltantes = len(faltantes)
        pct_cubierto = 0.0
    else:
        faltantes = pd.DatetimeIndex([])
        n_faltantes = 0
        pct_cubierto = None

    gaps_indices = _compactar_gaps(faltantes)

    # ── 2. Clima AgERA5 ───────────────────────────────────────────────────
    print(f"\n  [CLIM] Datos climáticos AgERA5 (reanálisis)...")
    fechas_bd_clima = _fechas_bd_clima(inicio_str, hoy_str)
    n_bd_clima = len(fechas_bd_clima)
    rango_dias = pd.date_range(inicio_str, hoy_str, freq="D")

    if not fechas_bd_clima.empty:
        pct_clima = round(100 * n_bd_clima / len(rango_dias), 1)
        print(f"    BD clima: {n_bd_clima} fecha(s) "
              f"[{fechas_bd_clima.min().date()} → {fechas_bd_clima.max().date()}] "
              f"({pct_clima}%)")
    else:
        pct_clima = 0.0
        print(f"    BD clima: 0 fechas (vacía)")

    # ── 3. Resumen ────────────────────────────────────────────────────────
    resumen = {
        "rango_inicio": inicio_str,
        "rango_fin": hoy_str,
        "indices": {
            "stac_desde": stac_desde,
            "stac_hasta": stac_hasta,
            "stac_total": len(fechas_stac),
            "bd_consultadas": n_bd,
            "pct_cubierto": pct_cubierto,
            "n_gaps": len(gaps_indices),
            "gaps": gaps_indices,
        },
        "clima": {
            "fechas_bd": n_bd_clima,
            "pct_cubierto": pct_clima,
            "bd_desde": str(fechas_bd_clima.min().date()) if n_bd_clima > 0 else None,
            "bd_hasta": str(fechas_bd_clima.max().date()) if n_bd_clima > 0 else None,
        },
    }

    # ── 4. Imprimir reporte ───────────────────────────────────────────────
    print(f"\n  {'=' * 50}")
    print(f"  RESUMEN DE COBERTURA")
    print(f"  {'=' * 50}")
    print(f"  Período: {inicio_str} → {hoy_str}")
    print()
    print(f"  ÍNDICES (EVI/LSWI)")
    if fechas_stac:
        print(f"    STAC (S2 reales): {len(fechas_stac)}")
    if n_bd > 0:
        print(f"    BD consultadas   : {n_bd} / {len(fechas_stac) if fechas_stac else '?'}")
    if pct_cubierto is not None:
        print(f"    Cobertura        : {pct_cubierto}%")
    if gaps_indices:
        print(f"    Gaps             : {len(gaps_indices)}")
        for g_ini, g_fin in gaps_indices[:10]:
            print(f"      • {g_ini} → {g_fin}")
        if len(gaps_indices) > 10:
            print(f"      ... y {len(gaps_indices) - 10} gap(s) más")
    else:
        print(f"    Gaps             : 0 — cobertura completa.")
    print()
    print(f"  CLIMA (AgERA5)")
    print(f"    BD   : {n_bd_clima} / {len(rango_dias)} días ({pct_clima}%)")
    print(f"  {'=' * 50}")

    return resumen
