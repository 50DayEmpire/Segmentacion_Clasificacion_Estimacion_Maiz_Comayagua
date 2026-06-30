from pathlib import Path
from utils.db import seeding

ROOT = Path(__file__).parent

def main():
    print("Hello from segmentacion-clasificacion-estimacion-maiz-comayagua!")
    seeding(str(ROOT / "data" / "PoligonosMaizPlayitas.geojson"))


if __name__ == "__main__":
    main()
