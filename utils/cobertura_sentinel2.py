import time
from pystac_client import Client
from datetime import date, datetime
from contextlib import closing
from config import ANIO_INICIAL_HISTORICO
from utils.conexionDB import get_connection_raw, get_db_path


def _partir_en_anios(fecha_inicio: str, fecha_fin: str) -> list[tuple[str, str]]:
    inicio = date.fromisoformat(fecha_inicio)
    fin = date.fromisoformat(fecha_fin)
    lotes: list[tuple[str, str]] = []
    for anio in range(inicio.year, fin.year + 1):
        l_ini = max(inicio, date(anio, 1, 1))
        if anio < fin.year:
            l_fin_str = f"{anio + 1}-01-01"
        else:
            l_fin_str = str(fin)
        lotes.append((str(l_ini), l_fin_str))
    return lotes


def _obtener_fechas_stac(bbox: list[float], fecha_inicio: str, fecha_fin: str) -> list[date]:
    max_retries = 3
    lotes = _partir_en_anios(fecha_inicio, fecha_fin)
    print(f"  [STAC] {len(lotes)} lote(s) anual(es) [{fecha_inicio} → {fecha_fin}]")
    todas: list[date] = []
    for l_ini, l_fin in lotes:
        print(f"  [STAC] Lote {l_ini[:4]}: S2 L2A bbox={bbox} [{l_ini} → {l_fin}]...")
        for intento in range(max_retries):
            try:
                catalogo = Client.open("https://stac.dataspace.copernicus.eu/v1")
                resultados = catalogo.search(
                    collections=["sentinel-2-l2a"],
                    bbox=bbox,
                    datetime=f"{l_ini}/{l_fin}",
                )
                items = list(resultados.items())
                break
            except Exception as exc:
                if intento < max_retries - 1:
                    espera = 2 ** intento
                    print(f"  [STAC] Error (intento {intento + 1}/{max_retries}): {exc}. Reintentando en {espera}s...")
                    time.sleep(espera)
                else:
                    raise
        fechas = sorted(set(item.datetime.date() for item in items))
        print(f"  [STAC] Lote {l_ini[:4]}: {len(fechas)} fecha(s).")
        todas.extend(fechas)
    fechas_validas = sorted(set(todas))
    print(f"  [STAC] Total: {len(fechas_validas)} fecha(s) de adquisición.")
    return fechas_validas


def actualizar_cobertura(bbox: list[float]) -> list[date]:
    """
    Ventana deslizante: consulta STAC desde la última fecha persistida + 1
    hasta hoy. Persiste las nuevas fechas en ``cobertura_sentinel2``.

    Parámetros
    ----------
    bbox : list[float]
        Bounding box [west, south, east, north] que envuelve todas las parcelas.

    Retorna
    -------
    list[date]
        Lista ordenada de **todas** las fechas de adquisición S2 conocidas
        (nuevas + previas), útil para reportes y detección de gaps.
    """
    with closing(get_connection_raw()) as conn:
        row = conn.execute("SELECT MAX(fecha) FROM cobertura_sentinel2").fetchone()
    ultima = date.fromisoformat(row[0]) if row and row[0] else None

    if ultima is None:
        fecha_ini = date(ANIO_INICIAL_HISTORICO, 1, 1)
    else:
        fecha_ini = ultima + __import__("datetime").timedelta(days=1)

    fecha_fin = date.today()
    print(f"  [COBERTURA] BD={get_db_path()} | ultima={ultima} | consultando STAC {fecha_ini} → {fecha_fin}")

    if fecha_ini > fecha_fin:
        # Ya estamos al día — devolver todas las fechas conocidas
        return _todas_fechas_cobertura()

    fechas_nuevas = _obtener_fechas_stac(
        bbox, fecha_ini.isoformat(), fecha_fin.isoformat()
    )

    if not fechas_nuevas:
        return _todas_fechas_cobertura()

    # Persistir solo las que no existen aún (la tabla no tiene UNIQUE en fecha)
    with closing(get_connection_raw()) as conn:
        existentes = set(
            r[0] for r in conn.execute(
                "SELECT fecha FROM cobertura_sentinel2 WHERE fecha BETWEEN ? AND ?",
                (fecha_ini.isoformat(), fecha_fin.isoformat()),
            ).fetchall()
        )
        nuevas = [f for f in fechas_nuevas if f.isoformat() not in existentes]
        if nuevas:
            with conn:
                conn.executemany(
                    "INSERT INTO cobertura_sentinel2 (fecha) VALUES (?)",
                    [(f.isoformat(),) for f in nuevas],
                )
            print(f"  [COBERTURA] Persistidas {len(nuevas)} fecha(s) nuevas en cobertura_sentinel2.")

    return _todas_fechas_cobertura()


def _todas_fechas_cobertura() -> list[date]:
    """Retorna todas las fechas en cobertura_sentinel2 ordenadas."""
    with closing(get_connection_raw()) as conn:
        rows = conn.execute(
            "SELECT fecha FROM cobertura_sentinel2 ORDER BY fecha"
        ).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]
