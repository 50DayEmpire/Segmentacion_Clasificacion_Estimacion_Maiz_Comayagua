# =========================================================================
# CONVERSIÓN DE KML A GEOJSON (SOPORTE GEOESPACIAL COMPLETO)
# =========================================================================
import geopandas as gpd
import fiona
from pathlib import Path

def kml_a_geojson(ruta_kml):
    ruta_kml = Path(ruta_kml)

    if ruta_kml.suffix.lower() != '.kml':
        raise ValueError('El archivo de entrada debe tener extensión .kml')

    if not ruta_kml.exists():
        raise FileNotFoundError(f'No se encontró el archivo KML en la ruta especificada: {ruta_kml}')

    # 1. Habilitar explícitamente el driver de KML en Fiona.
    fiona.drvsupport.supported_drivers['KML'] = 'rw'

    # 2. Definir las rutas de salida en la misma carpeta del KML.
    carpeta_salida = ruta_kml.parent
    nombre_base = ruta_kml.stem
    path_output_geojson_wgs84 = carpeta_salida / f'{nombre_base}.geojson'
    path_output_geojson_utm16n = carpeta_salida / f'{nombre_base}_UTM16N.geojson'

    print(f"Leyendo archivo KML desde: {ruta_kml}...")

    # 3. Cargar el KML usando el driver activado.
    # Nota: Si tu KML tiene múltiples capas, puedes pasar el argumento layer='NombreDeCapa'
    gdf_kml = gpd.read_file(ruta_kml, driver='KML')
    print(f"-> Éxito: Se cargaron {len(gdf_kml)} geometrías desde el KML.")
    print(f"-> Sistema de Coordenadas Original: {gdf_kml.crs}")

    # 4. Guardar en formato GeoJSON Estándar (WGS84 - Grados).
    gdf_kml.to_file(path_output_geojson_wgs84, driver='GeoJSON')
    print(f"✅ Archivo GeoJSON (Grados - EPSG:4326) guardado en: {path_output_geojson_wgs84}")

    # 5. Reproyectar y guardar inmediatamente en Metros (UTM 16N - EPSG:32616).
    # Esto te ahorra tener que reproyectarlo en cada ejecución de tus otras celdas.
    gdf_utm = gdf_kml.to_crs(epsg=32616)
    gdf_utm.to_file(path_output_geojson_utm16n, driver='GeoJSON')
    print(f"✅ Archivo GeoJSON (Metros - EPSG:32616) guardado en: {path_output_geojson_utm16n}")

    print("\n¡Conversión y ordenamiento geoespacial completado!")

    return {
        'kml': str(ruta_kml),
        'geojson_wgs84': str(path_output_geojson_wgs84),
        'geojson_utm16n': str(path_output_geojson_utm16n),
    }