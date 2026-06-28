import os
import xarray as xr

def descargar_datacube(datacube, ruta_netcdf="cubo_temporal_rgb.nc", formato="NetCDF"):
    """
    Descarga un datacube desde openEO y lo guarda como archivo NetCDF.
    
    Parámetros:
    - datacube: objeto openEO datacube ya configurado
    - ruta_netcdf: str, nombre del archivo de salida
    - formato: str, formato de exportación (por defecto "NetCDF")
    
    Retorna:
    - ruta del archivo descargado si fue exitoso, None si falló
    """
    # Blindaje contra corrupción: eliminar archivo previo
    if os.path.exists(ruta_netcdf):
        print(f"🗑️ Detectado archivo anterior '{ruta_netcdf}'. Eliminando para evitar corrupción...")
        try:
            os.remove(ruta_netcdf)
            print("✅ Archivo antiguo removido con éxito.")
        except Exception as e:
            print(f"⚠️ No se pudo eliminar el archivo (puede estar bloqueado por xarray): {e}")
            print("💡 Consejo: Si el error persiste, reinicia el entorno de ejecución en Colab.")
            return None

    print("⏳ Descargando datos desde openEO a un archivo NetCDF local...")
    try:
        datacube.download(ruta_netcdf, format=formato)
        print("✅ ¡Éxito! Archivo NetCDF descargado y guardado correctamente.")
        return ruta_netcdf
    except Exception as e:
        print(f"❌ Error durante la descarga de openEO: {e}")
        return None
