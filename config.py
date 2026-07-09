# config.py — Constantes globales del observatorio
from pathlib import Path

# ── Rutas ──────────────────────────────────────────────────────────────────────
ROOT               = Path(__file__).parent
GPKG_PATH          = ROOT / "data" / "pipeline.gpkg"
# MUNICIPIO_GEOJSON  = ROOT / "static" / "ComayaguaMunicipio.geojson"
MUNICIPIO_GEOJSON  = ROOT / "static" / "ValleComayagua.geojson"

# ── Endpoints ──────────────────────────────────────────────────────────────────
OPENEO = "openeo.dataspace.copernicus.eu"
OPENEOFED = "openeofed.dataspace.copernicus.eu"

# ── Capas del GeoPackage ───────────────────────────────────────────────────────
LAYERS_GPKG = {
    "parcelas":    "parcelas_vigentes",   # nombre real en el gpkg tras el seeding
    "ciclos":      "ciclos",
    "gpp_diario":  "gpp_diario",
    "rendimiento": "rendimiento",
    "serie_evi":   "serie_evi",
    "serie_lswi":  "serie_lswi",
}

# ── Ciclos de siembra ──────────────────────────────────────────────────────────
CICLOS = {
    "Primera (abril-julio)":  "primera", #Segun CAN 2024
    "Postrera (agosto-marzo)": "postrera",
}

FECHA_SIEMBRA_CICLOS = {
    "primera":  {"inicio": (4, 1), "fin": (7, 31)},  # abril 1 - julio 31
    "postrera": {"inicio": (8, 1), "fin": (3, 31)},  # agosto 1 - marzo 31
}

DURACION_CICLO = 120 # días — duración típica de un ciclo de cultivo de maíz en Comayagua

DURACION_MAX_CICLO = 160 # días — duración máxima de un ciclo de cultivo de maíz en Comayagua

# ── Años disponibles para análisis histórico ────────────────────────────────────
ANIOS_HISTORICO = [2020, 2021, 2022, 2023, 2024, 2025]
ANIO_INICIAL_HISTORICO = 2020

# ── Ventanas de predicción ─────────────────────────────────────────────────────
VENTANAS = ["T1", "T2", "T3", "EOS"]

DIAS_VENTANAS = {
    "T1":  30,  # días desde el inicio de temporada (SOS)
    "T2":  60,  # días desde el inicio de temporada (SOS)
    "T3":  90,  # días desde el inicio de temporada (SOS)
    "EOS": 120, # ventana de predicción EOS
}

# ---- Parámetros para corrección de DN y cálculo de reflectancia real -----------------

ESCALA = 10000  # Escala de reflectancia usada en los productos Sentinel-2
BOA_OFFSET = -1000

# ── Colores por ciclo ──────────────────────────────────────────────────────────
COLOR_PRIMERA  = "#2ecc71"   # verde
COLOR_POSTRERA = "#e67e22"   # naranja

COLORES_CICLO = {
    "primera":  COLOR_PRIMERA,
    "postrera": COLOR_POSTRERA,
}

# ── Colores por cultivo clasificado ────────────────────────────────────────────
COLORES_CULTIVO = {
    "maiz":       "#f1c40f",
    "otro":       "#95a5a6",
    "sin_datos":  "#2c3e50",
}

# ── Escala de rendimiento (qq/ha) ──────────────────────────────────────────────
RENDIMIENTO_MIN_QQ_HA = 0
RENDIMIENTO_MAX_QQ_HA = 120

# ── Parámetros del mapa Folium ─────────────────────────────────────────────────
MAPA_CENTRO_LAT  = 14.477    # centro geográfico del municipio de Comayagua
MAPA_CENTRO_LON  = -87.641
MAPA_ZOOM_INICIO = 11        # zoom adecuado para ver el municipio completo
MAPA_TILES       = "CartoDB dark_matter"

# ── Bounding box del municipio de Comayagua (EPSG:4326) ───────────────────────
MUNICIPIO_BOUNDS = [-87.897, 14.357, -87.385, 14.597]  # [minx, miny, maxx, maxy]

# ── CRS ───────────────────────────────────────────────────────────────────────
CRS_METRICO    = "EPSG:32616"
CRS_GEOGRAFICO = "EPSG:4326"

# ── Referencia SAG/CAN (qq/ha por ciclo) ──────────────────────────────────────
RENDIMIENTO_REF = {
    "primera":  45.0,
    "postrera": 38.0,
}

# ── Métricas de validación a mostrar ──────────────────────────────────────────
METRICAS_VALIDACION = ["RMSE", "MAE", "MAPE", "R²"]
