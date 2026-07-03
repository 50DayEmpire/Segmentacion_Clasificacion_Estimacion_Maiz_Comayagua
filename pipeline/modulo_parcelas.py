import subprocess
import os
from pathlib import Path
import geopandas as gpd

# --- Configuración de Rutas Relativas ---
# Asumiendo que ejecutas desde la raíz del proyecto
_REPO_ROOT = Path(__file__).resolve().parents[1]  # Ajusta si tu estructura difiere

DELINEATE_ANYTHING_ROOT = _REPO_ROOT / "delineate_anything"
DELINEATE_BATCH_CONFIG = DELINEATE_ANYTHING_ROOT / "batch_sample.yaml"
DELINEATE_SCRIPT = DELINEATE_ANYTHING_ROOT / "delineate.py"

# En lugar de buscar un .venv local, apuntamos dinámicamente al Conda del sistema
CONDA_EXE = os.environ.get("CONDA_EXE", r"C:\Users\mayno\miniconda3\Scripts\conda.exe")
CONDA_ENV_NAME = "tesis_maiz"


def _load_spatial_extent(geojson_path: Path) -> dict:
    gdf = gpd.read_file(geojson_path)
    if gdf.empty:
        raise ValueError(f"No se encontraron geometrías en {geojson_path}")

    geometry = gdf.geometry.unary_union
    return geometry.__geo_interface__


def ejecutar_delineate_anything_local(
    batch_config: Path | str = DELINEATE_BATCH_CONFIG,
) -> None:
    """Ejecuta Delineate-Anything localmente dentro del entorno Conda 'tesis_maiz' con GPU."""
    batch_config_path = Path(batch_config)
    
    # Validaciones previas de archivos
    if not batch_config_path.exists():
        raise FileNotFoundError(f"No existe el archivo de batch: {batch_config_path}")
    if not DELINEATE_SCRIPT.exists():
        raise FileNotFoundError(f"No se encontró el script de delineación en: {DELINEATE_SCRIPT}")
    if not os.path.exists(CONDA_EXE):
        raise FileNotFoundError(f"No se detectó el ejecutable de Conda en la ruta especificada: {CONDA_EXE}")

    print(f" Lanzando inferencia por lotes en entorno Conda '{CONDA_ENV_NAME}' usando GPU...")
    print(f"Configuración utilizada: {batch_config_path.name}")

    # La magia en Windows: Usamos 'conda run -n nombre_entorno'
    # Esto inicializa CUDA, GDAL y todas las variables nativas automáticamente
    comando = [
        CONDA_EXE, "run", 
        "-n", CONDA_ENV_NAME, 
        "--no-capture-output",  # Para que veas el progreso de YOLO en tiempo real en tu consola
        "python", str(DELINEATE_SCRIPT), 
        "-b", str(batch_config_path)
    ]

    try:
        subprocess.run(
            comando,
            cwd=str(DELINEATE_ANYTHING_ROOT),
            check=True
        )
        print("\n Inferencia por lotes completada con éxito.")
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] El script falló durante la ejecución. Código de salida: {e.returncode}")
        raise e