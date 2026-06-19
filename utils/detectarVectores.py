# =========================================================================
# CELDA: CONTEO Y ANÁLISIS DE POLÍGONOS EN GEOJSON
# =========================================================================
import geopandas as gpd
import os

def detectar_vectores(ruta):
    print(f"Abriendo archivo: {ruta}...\n")

    if not os.path.exists(ruta):
        print("⚠️ ERROR: El archivo GeoJSON no existe en la ruta especificada.")
    else:
        # 2. Cargar el archivo vector
        gdf = gpd.read_file(ruta)
        
        # 3. Obtener el número total de filas (Features)
        total_features = len(gdf)
        
        # 4. Desglosar por tipo de geometría (por si hay MultiPolygons o líneas por error)
        conteo_geometrias = gdf.geom_type.value_counts()
        
        # =========================================================================
        # REPORTE EN CONSOLA
        # =========================================================================
        print("="*50)
        print("          REPORTE DE ESTRUCTURA VECTORIAL          ")
        print("="*50)
        print(f"Número total de registros (filas): {total_features}")
        print("-"*50)
        print("Conteo detallado por tipo de geometría:")
        for tipo, cantidad in conteo_geometrias.items():
            print(f"  - {tipo}: {cantidad}")
        print("="*50)
        
        # Verificación rápida de datos
        if 'Polygon' in conteo_geometrias or 'MultiPolygon' in conteo_geometrias:
            print("✅ El archivo contiene geometrías válidas para tu validación.")
        else:
            print("⚠️ ALERTA: No se detectaron polígonos en este archivo.")