from __future__ import annotations

from datetime import date
from contextlib import closing

import pandas as pd
import openeo

from config import ANIO_INICIAL_HISTORICO, OPENEO, OPENEOFED
from utils.conexionDB import get_connection_raw


def _conectar_cdse() -> openeo.Connection:
    return openeo.connect(f"https://{OPENEO}").authenticate_oidc()


def _conectar_fed() -> openeo.Connection:
    return openeo.connect(f"https://{OPENEOFED}").authenticate_oidc()


def _rango_completo() -> tuple[date, date]:
    hoy = date.today()
    inicio = date(ANIO_INICIAL_HISTORICO, 1, 1)
    return inicio, hoy


def _fechas_bd_indices(
    fecha_inicio: str,
    fecha_fin: str,
) -> pd.DatetimeIndex:
    """
    Retorna las fechas que ya tienen fila en ``series_diarias_vpm``
    (hayan sido consultadas al servidor, tengan o no datos válidos).
    """
    sql = """
        SELECT DISTINCT fecha
        FROM series_diarias_vpm
        WHERE fecha BETWEEN ? AND ?
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


def reporte_cobertura() -> dict:
    """
    Genera un reporte de cobertura comparando las fechas disponibles en BD
    contra el rango histórico completo para índices satelitales y datos climáticos.

    Para índices consulta el servidor openEO CDSE para conocer el rango temporal
    de la colección Sentinel-2 L2A.

    Para clima (AgERA5) asume cobertura total por ser un reanálisis.

    Retorna
    -------
    dict con secciones ``indices``, ``clima`` y ``resumen``.
    """
    inicio, hoy = _rango_completo()
    inicio_str = inicio.isoformat()
    hoy_str = hoy.isoformat()
    rango_completo_dias = pd.date_range(inicio_str, hoy_str, freq="D")

    print(f"\n  Rango histórico: [{inicio_str} → {hoy_str}]")

    # ── 1. Índices Sentinel-2 ─────────────────────────────────────────────
    print("\n  [SAT] Consultando servidor CDSE (colección SENTINEL2_L2A)...")
    rango_servidor: tuple[date | None, date | None] = (None, None)
    try:
        conn_cdse = _conectar_cdse()
        meta = conn_cdse.describe_collection("SENTINEL2_L2A")
        te = meta.get("temporal_extent", [None, None])
        if te and te[0]:
            rango_servidor = (
                pd.Timestamp(te[0]).date(),
                pd.Timestamp(te[1]).date() if te[1] else hoy,
            )
            print(f"    Servidor: [{rango_servidor[0]} → {rango_servidor[1]}]")
        else:
            print(f"    Servidor: metadata sin temporal_extent.")
    except Exception as e:
        print(f"    Error consultando servidor CDSE: {e}")

    fechas_bd_indices = _fechas_bd_indices(inicio_str, hoy_str)
    n_bd_indices = len(fechas_bd_indices)

    if not fechas_bd_indices.empty:
        print(f"    BD índices: {n_bd_indices} fecha(s) "
              f"[{fechas_bd_indices.min().date()} → {fechas_bd_indices.max().date()}]")
    else:
        print(f"    BD índices: 0 fechas (vacía)")

    # Gaps
    if n_bd_indices > 0:
        faltantes = rango_completo_dias.difference(fechas_bd_indices, sort=False)
        n_faltantes = len(faltantes)
        pct_cubierto = round(100 * n_bd_indices / len(rango_completo_dias), 1)
    else:
        faltantes = rango_completo_dias
        n_faltantes = len(rango_completo_dias)
        pct_cubierto = 0.0

    # Compactar gaps en rangos
    gaps_indices: list[tuple[str, str]] = []
    if n_faltantes > 0:
        i = 0
        while i < n_faltantes:
            g_ini = faltantes[i]
            g_fin = g_ini
            j = i + 1
            while j < n_faltantes and (faltantes[j] - faltantes[j - 1]).days == 1:
                g_fin = faltantes[j]
                j += 1
            gaps_indices.append((str(g_ini.date()), str(g_fin.date())))
            i = j

    # ── 2. Clima AgERA5 ───────────────────────────────────────────────────
    print(f"\n  [CLIM] Datos climáticos AgERA5 (reanálisis, cobertura total asumida)...")
    fechas_bd_clima = _fechas_bd_clima(inicio_str, hoy_str)
    n_bd_clima = len(fechas_bd_clima)

    if not fechas_bd_clima.empty:
        pct_clima = round(100 * n_bd_clima / len(rango_completo_dias), 1)
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
        "dias_totales": len(rango_completo_dias),
        "indices": {
            "fechas_bd": n_bd_indices,
            "pct_cubierto": pct_cubierto,
            "n_gaps": len(gaps_indices),
            "gaps": gaps_indices,
            "bd_desde": str(fechas_bd_indices.min().date()) if n_bd_indices > 0 else None,
            "bd_hasta": str(fechas_bd_indices.max().date()) if n_bd_indices > 0 else None,
            "servidor_desde": str(rango_servidor[0]) if rango_servidor[0] else None,
            "servidor_hasta": str(rango_servidor[1]) if rango_servidor[1] else None,
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
    print(f"  Período total: {inicio_str} → {hoy_str} ({len(rango_completo_dias)} días)")
    print()
    print(f"  ÍNDICES (EVI/LSWI)")
    print(f"    BD      : {n_bd_indices} / {len(rango_completo_dias)} días ({pct_cubierto}%)")
    if gaps_indices:
        print(f"    Gaps    : {len(gaps_indices)}")
        for g_ini, g_fin in gaps_indices[:10]:
            print(f"      • {g_ini} → {g_fin}")
        if len(gaps_indices) > 10:
            print(f"      ... y {len(gaps_indices) - 10} gap(s) más")
    else:
        print(f"    Gaps    : 0 — cobertura completa.")
    print()
    print(f"  CLIMA (AgERA5)")
    print(f"    BD      : {n_bd_clima} / {len(rango_completo_dias)} días ({pct_clima}%)")
    print(f"  {'=' * 50}")

    return resumen
