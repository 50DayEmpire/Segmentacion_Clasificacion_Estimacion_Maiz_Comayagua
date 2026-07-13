import http.server
import json
import io
import logging
import math
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import fiona
import geopandas as gpd
from utils.conexionDB import get_db_path

logger = logging.getLogger(__name__)

SAVE_API_PORT = 8765
_server_instance = None
_CAPA = "parcelas_vigentes"


def _calcular_area_en_m2(gdf: gpd.GeoDataFrame) -> "pd.Series":
    gdf = gdf.copy()
    if gdf.crs is None or gdf.crs.is_geographic:
        gdf = gdf.set_crs("EPSG:4326")
        gdf = gdf.to_crs(gdf.estimate_utm_crs())
    return gdf.geometry.area


def _normalizar_columnas(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Asegura que el GeoDataFrame tenga las columnas id, bg, area."""
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")

    if "id" not in gdf.columns:
        gdf["id"] = range(1, len(gdf) + 1)
    else:
        max_id = gdf["id"].max()
        if not isinstance(max_id, (int, float)) or math.isnan(max_id):
            max_id = 0
        missing = gdf["id"].isna()
        if missing.any():
            n = missing.sum()
            gdf.loc[missing, "id"] = range(int(max_id) + 1, int(max_id) + n + 1)
        gdf["id"] = gdf["id"].astype(int)

    if "bg" not in gdf.columns:
        gdf["bg"] = False
    else:
        gdf["bg"] = gdf["bg"].fillna(False)
    gdf["bg"] = gdf["bg"].astype(bool)

    if "area" not in gdf.columns:
        gdf["area"] = _calcular_area_en_m2(gdf)
    else:
        missing = gdf["area"].isna()
        if missing.any():
            gdf.loc[missing, "area"] = _calcular_area_en_m2(gdf.loc[missing])

    return gdf


def _reemplazar_capa(gdf: gpd.GeoDataFrame, gpkg_path: str, layer: str = _CAPA) -> int:
    """Reemplaza limpiamente una capa en GPKG usando pyogrio.

    - DROP TABLE de la(s) capa(s) nuestra(s) (SQLite cascadea el rtree)
    - pyogrio write_dataframe crea capa fresca
    - VACUUM recupera espacio de páginas libres
    """
    path = Path(gpkg_path)
    with closing(sqlite3.connect(str(path))) as conn:
        with conn:
            conn.execute(f'DROP TABLE IF EXISTS "{layer}"')
            conn.execute("DELETE FROM gpkg_contents WHERE table_name = ?", (layer,))
            conn.execute("DELETE FROM gpkg_geometry_columns WHERE table_name = ?", (layer,))
            conn.execute("DELETE FROM gpkg_ogr_contents WHERE table_name = ?", (layer,))
            conn.execute("DROP TABLE IF EXISTS \"fields\"")
            conn.execute("DELETE FROM gpkg_contents WHERE table_name = 'fields'")
            conn.execute("DELETE FROM gpkg_geometry_columns WHERE table_name = 'fields'")
            conn.execute("DELETE FROM gpkg_ogr_contents WHERE table_name = 'fields'")
    gdf = _normalizar_columnas(gdf)
    import pyogrio
    pyogrio.write_dataframe(gdf, str(path), layer=layer, driver="GPKG")
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("VACUUM")
    return len(gdf)


def _diagnosticar(path: str) -> dict:
    info = {"path": path, "exists": Path(path).exists(), "layers": {}}
    if not info["exists"]:
        return info
    for capa in fiona.listlayers(path):
        try:
            src = gpd.read_file(path, layer=capa)
            info["layers"][capa] = len(src)
        except Exception:
            info["layers"][capa] = -1
    return info


class SaveHandler(http.server.BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        origin = self.headers.get("Origin", "*")
        if self.path == "/diagnose":
            archivo = self.headers.get("X-Gpkg-Path", "")
            info = _diagnosticar(archivo) if archivo else {"error": "X-Gpkg-Path header required"}
            self._send_json(200, info, origin)
        else:
            self._send_json(404, {"error": "not found"}, origin)

    def do_POST(self):
        origin = self.headers.get("Origin", "*")
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length)

        try:
            data = json.loads(raw)
            geojson_str = data.get("geojson")
            archivo_gpkg = data.get("archivo_gpkg")

            if not geojson_str or not archivo_gpkg:
                raise ValueError("Missing 'geojson' or 'archivo_gpkg'")

            gpkg_path = Path(archivo_gpkg)
            if not gpkg_path.exists():
                raise FileNotFoundError(f"GPKG no encontrado: {archivo_gpkg}")

            activa = Path(get_db_path()).resolve()
            if gpkg_path.resolve() == activa:
                raise PermissionError(
                    f"Negado: no se puede escribir sobre la BD activa ({activa.name}). "
                    f"Usa 'Validar a producci\u00f3n' en el sidebar."
                )

            gdf = gpd.read_file(io.StringIO(geojson_str))
            prev = _diagnosticar(archivo_gpkg)
            n = _reemplazar_capa(gdf, archivo_gpkg)
            post = _diagnosticar(archivo_gpkg)
            logger.info(
                "Save OK: %d features -> %s (capas ANTES=%s DESPUES=%s)",
                n, gpkg_path.name, prev["layers"], post["layers"],
            )
            self._send_json(200, {"status": "ok", "features": n}, origin)
        except Exception as e:
            logger.error("Save error: %s", e)
            self._send_json(500, {"status": "error", "message": str(e)}, origin)

    def _send_json(self, status, data, origin="*"):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        logger.debug("Save API: %s", fmt % args)


def start_save_api():
    global _server_instance
    if _server_instance is not None:
        return
    server = http.server.ThreadingHTTPServer(("localhost", SAVE_API_PORT), SaveHandler)
    server.allow_reuse_address = True
    _server_instance = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Save API server started on port %d", SAVE_API_PORT)


def stop_save_api():
    global _server_instance
    if _server_instance is not None:
        _server_instance.shutdown()
        _server_instance = None
