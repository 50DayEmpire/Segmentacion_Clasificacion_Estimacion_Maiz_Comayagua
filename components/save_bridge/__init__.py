from pathlib import Path
import streamlit.components.v1 as components

_SAVE_BRIDGE = components.declare_component(
    "save_bridge",
    path=str(Path(__file__).parent / "frontend"),
)


def render_save_bridge(key: str | None = None) -> str | None:
    """Renderiza un bridge invisible que captura postMessage del mapa.

    Retorna el GeoJSON FeatureCollection cuando el usuario presiona
    Guardar en el toolbar del mapa, o None si no hay datos pendientes.
    """
    return _SAVE_BRIDGE(key=key, default=None)
