# pages/8_Segmentacion_Parcelas.py — Administración: segmentación de parcelas
import os
import subprocess
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import streamlit as st
from components.mapa_segmentacion import render_mapa_segmentacion, limpiar_cache as limpiar_cache_html
from components.sidebar_filtros import render_filtros_segmentacion
from config import CAPAS_SEGMENTACION
from utils.save_api import start_save_api

start_save_api()


# ── Rutas para ejecución del modelo ────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DELINEATE_ROOT = _REPO_ROOT / "delineate_anything"
_DELINEATE_SCRIPT = _DELINEATE_ROOT / "delineate.py"
_BATCH_CONFIG = _DELINEATE_ROOT / "batch_sample.yaml"
_CONDA_EXE = os.environ.get("CONDA_EXE", r"C:\Users\mayno\miniconda3\Scripts\conda.exe")
_CONDA_ENV = "tesis_maiz"


@st.cache_data(show_spinner=False)
def _cargar_gpkg_cache(ruta: str) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(ruta, layer="parcelas_vigentes")
    except Exception:
        return gpd.read_file(ruta)


st.markdown("## 🌱 Parcelas")
st.markdown(
    "Visualiza y edita las capas de polígonos generadas por el modelo de segmentación "
    "([delineate-anything](https://github.com/anomalyco/delineate-anything)). "
    "Selecciona una capa en el panel izquierdo. Los polígonos se pueden editar "
    "con las herramientas del mapa."
)

@st.dialog("Ejecutar modelo de segmentación")
def _dialogo_confirmar():
    st.warning(
        "El modelo DelineateAnything es computacionalmente pesado y "
        "puede tardar varios minutos en completarse. La aplicación "
        "quedará bloqueada durante la ejecución."
    )
    st.info("Usa la configuración predefinida en `batch_sample.yaml` (incluye simplificación).")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Continuar", type="primary", use_container_width=True):
            st.session_state.mostrar_confirmacion = False
            st.session_state.ejecutar_modelo = True
            st.rerun()
    with col2:
        if st.button("Cancelar", use_container_width=True):
            st.session_state.mostrar_confirmacion = False
            st.rerun()


if st.session_state.get("mostrar_confirmacion", False):
    _dialogo_confirmar()

if st.session_state.pop("ejecutar_modelo", False):
    with st.status("Ejecutando delineate-anything...", expanded=True) as status:
        comando = [
            _CONDA_EXE, "run",
            "-n", _CONDA_ENV,
            "--no-capture-output",
            "python", str(_DELINEATE_SCRIPT),
            "-b", str(_BATCH_CONFIG),
            "--suffix", f"_{datetime.now():%Y%m%d_%H%M%S}",
        ]
        proc = subprocess.Popen(
            comando,
            cwd=str(_DELINEATE_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
            encoding="utf-8",
        )
        for line in proc.stdout:
            st.write(line.rstrip())
        proc.wait()
        if proc.returncode != 0:
            st.error(f"Error en segmentación (código {proc.returncode})")
            st.stop()
        for f in Path(CAPAS_SEGMENTACION).glob("*.gpkg"):
            if not f.name.endswith(".simp.gpkg"):
                f.unlink(missing_ok=True)
        status.update(label="✅ Segmentación completada", state="complete")
    st.success("Modelo ejecutado exitosamente. Revisa los borradores en el panel izquierdo.")
    _cargar_gpkg_cache.clear()
    limpiar_cache_html()
    st.rerun()

st.divider()

with st.sidebar:
    capa_info = render_filtros_segmentacion()
    st.divider()
    if st.button("🔄 Limpiar caché", use_container_width=True, help="Recarga la capa desde el archivo"):
        _cargar_gpkg_cache.clear()
        limpiar_cache_html()
        st.rerun()

gdf_capa = None
nombre_capa = None
if capa_info["archivo_gpkg"] and Path(capa_info["archivo_gpkg"]).exists():
    try:
        gdf_capa = _cargar_gpkg_cache(capa_info["archivo_gpkg"])
        nombre_capa = capa_info["nombre_capa"]
    except Exception as e:
        st.error(f"Error al leer la capa: {e}", icon="❌")

resultado = render_mapa_segmentacion(
    gdf=gdf_capa,
    nombre_capa=nombre_capa,
    archivo_gpkg=capa_info.get("archivo_gpkg"),
)

if gdf_capa is not None and not gdf_capa.empty:
    st.divider()
    st.caption(
        "Usa los botones del mapa (esquina superior izquierda): "
        "✏️ editar vértices, ⬡ dibujar polígono, ▬ dibujar rectángulo, "
        "🗑️ eliminar seleccionado, 💾 descargar GeoJSON. "
        "Clic en un polígono para seleccionarlo; tecla **Supr** para eliminarlo. "
        "Presiona **💾 Guardar** para persistir en el GPKG. "
        "Luego presiona **🔄 Limpiar caché** en el panel izquierdo para recargar."
    )
