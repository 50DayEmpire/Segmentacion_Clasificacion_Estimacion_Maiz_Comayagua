from pathlib import Path
import sys
import subprocess

import geopandas as gpd
import openeo

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from delineate_anything.openeo_udp.process_graph.delineate_onnx import (
    DEFAULT_JOB_OPTIONS,
    build_delineate_full,
)


BACKEND = "https://openeo.dataspace.copernicus.eu"
VALLE_GEOJSON = _REPO_ROOT / "data" / "ValleComayagua.geojson"
OUTPUT_FILE = _REPO_ROOT / "data" / "delineate_fields.gpkg"
TEMPORAL_EXTENT = ["2024-04-01", "2024-09-30"]
DELINEATE_ANYTHING_ROOT = _REPO_ROOT / "delineate_anything"
DELINEATE_BATCH_CONFIG = DELINEATE_ANYTHING_ROOT / "batch_sample.yaml"
DELINEATE_SCRIPT = DELINEATE_ANYTHING_ROOT / "delineate.py"
DELINEATE_PYTHON = DELINEATE_ANYTHING_ROOT / ".venv" / "Scripts" / "python.exe"


def _load_spatial_extent(geojson_path: Path) -> dict:
    gdf = gpd.read_file(geojson_path)
    if gdf.empty:
        raise ValueError(f"No se encontraron geometrías en {geojson_path}")

    geometry = gdf.geometry.unary_union
    return geometry.__geo_interface__


def ejecutar_delineate_anything_local(
    batch_config: Path | str = DELINEATE_BATCH_CONFIG,
) -> None:
    """Ejecuta Delineate-Anything localmente usando el batch YAML del README."""
    batch_config_path = Path(batch_config)
    if not batch_config_path.exists():
        raise FileNotFoundError(f"No existe el archivo de batch: {batch_config_path}")
    if not DELINEATE_PYTHON.exists():
        raise FileNotFoundError(f"No existe el python del venv de Delineate-Anything: {DELINEATE_PYTHON}")

    subprocess.run(
        [str(DELINEATE_PYTHON), str(DELINEATE_SCRIPT), "-b", str(batch_config_path)],
        cwd=str(DELINEATE_ANYTHING_ROOT),
        check=True,
    )


def main() -> None:
    ejecutar_delineate_anything_local()


if __name__ == "__main__":
    main()
