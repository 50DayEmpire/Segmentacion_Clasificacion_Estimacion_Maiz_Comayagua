# components/mapa_segmentacion.py — Mapa Folium estático con Leaflet.Editable
"""
Renderiza mapa Folium estático sin re-runs en pan/zoom.

El HTML del mapa se cachea por ruta del .gpkg + hash del código.
La edición se maneja con Leaflet.Editable inyectado en el HTML,
100% client-side.
"""
import hashlib
import inspect
import re

import streamlit as st
import folium
from folium.plugins import Fullscreen
from shapely import force_2d

from config import (
    MAPA_CENTRO_LAT,
    MAPA_CENTRO_LON,
    MAPA_ZOOM_INICIO,
    MAPA_TILES,
)
from utils.queries import cargar_municipio
from utils.capas_folium import agregar_capa_poligonos


def _build_toolbar_js(archivo_gpkg: str = "") -> str:
    """Genera el JavaScript toolbar + Leaflet.Editable (usa window.__folium_map)."""
    _GPKG_PH = "___ARCHIVO_GPKG___"
    _API_URL_PH = "___SAVE_API_URL___"
    _save_api_url = "http://localhost:8765/save"

    tool_html = """<div id="leetoolbar">
  <button class="ltb" onclick="ltoToggleEdit()" id="ltb-edit" title="Editar v\u00e9rtices">\u270f\ufe0f</button>
  <button class="ltb" onclick="ltoDrawPolygon()" title="Dibujar pol\u00edgono">\u2b21</button>
  <button class="ltb" onclick="ltoDrawRectangle()" title="Dibujar rect\u00e1ngulo">\u25ac</button>
  <button class="ltb" id="ltb-delete" onclick="ltoToggleDeleteMode()" title="Eliminar pol\u00edgono (clic para activar/desactivar modo)">\U0001F5D1</button>
  <button class="ltb" onclick="ltoDownload()" title="Descargar GeoJSON">\U0001F4E5</button>
  <button class="ltb ltb-save" onclick="ltoSaveToGpkg()" title="Guardar cambios en GPKG">\U0001F4BE Guardar</button>
  <a id="ltb-dl-link" style="display:none"></a>
</div>"""

    css = """<style>
  #leetoolbar {position:absolute;top:130px;left:12px;z-index:1000;display:flex;flex-direction:column;gap:4px;}
  .ltb {width:36px;height:36px;border:1px solid #2d3139;border-radius:6px;background:#1a1d23;color:#eee;font-size:1rem;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background .15s;box-shadow:0 2px 6px rgba(0,0,0,.3);}
  .ltb:hover {background:#2d3139;}
  .ltb.active {background:#2980b9;border-color:#3498db;}
  .ltb-save {width:auto;padding:0 12px;gap:4px;background:#27ae60;border-color:#2ecc71;margin-top:4px;}
  .ltb-save:hover {background:#2ecc71;}
</style>"""

    import pathlib
    _le_path = pathlib.Path(__file__).parent / "mapa_segmentacion" / "static" / "Leaflet.Editable.js"
    _le_js = _le_path.read_text(encoding="utf-8")
    le_script_tag = "<script>" + _le_js + "</script>"
    js = le_script_tag + """
<script>
(function(){
var map = window.__folium_map;
if (!map) { console.error('LTO: __folium_map not set'); return; }

var selectedLayer = null;
var editMode = false;
var editableReady = false;
var DRAW_GROUP = null;
var deleteMode = false;

try {
  if (typeof L.Editable !== 'undefined') {
    map.editTools = new L.Editable(map);
    editableReady = true;
  }
} catch(e) { console.warn('LTO: Editable init failed', e); }

var origStyle = {fillColor:'#3498db',color:'#2980b9',weight:2,fillOpacity:0.3};
var hiStyle   = {fillColor:'#e74c3c',color:'#c0392b',weight:3,fillOpacity:0.4};

// Toolbar functions — assigned first so they always exist
window.ltoToggleEdit = function() {
  if (!editableReady || !selectedLayer) return;
  editMode = !editMode;
  if (editMode) {
    try { selectedLayer.enableEdit(); } catch(e) {}
    document.getElementById('ltb-edit').classList.add('active');
  } else {
    try { if (selectedLayer.editor) selectedLayer.disableEdit(); } catch(e) {}
    document.getElementById('ltb-edit').classList.remove('active');
  }
};

window.ltoDrawPolygon = function() {
  if (!editableReady) { console.warn('LTO: not ready'); return; }
  console.log('LTO: startPolygon called');
  deselect();
  try {
    var p = map.editTools.startPolygon();
    console.log('LTO: startPolygon returned', p && p._leaflet_id);
  } catch(e) { console.error('LTO: startPolygon error', e); }
};

window.ltoDrawRectangle = function() {
  if (!editableReady) return;
  console.log('LTO: startRectangle called');
  deselect();
  try {
    map.editTools.startRectangle();
  } catch(e) { console.error('LTO: startRectangle error', e); }
};

function deletePolygon(layer) {
  (function removeFromParents(parent) {
    if (!parent || !parent.eachLayer) return;
    parent.eachLayer(function(child) {
      if (child === layer) {
        parent.removeLayer(child);
      } else if (child.eachLayer) {
        removeFromParents(child);
      }
    });
  })(map);
  if (DRAW_GROUP) DRAW_GROUP.removeLayer(layer);
  if (selectedLayer === layer) {
    selectedLayer = null;
  }
}

window.ltoToggleDeleteMode = function() {
  deleteMode = !deleteMode;
  if (deleteMode) {
    document.getElementById('ltb-delete').classList.add('active');
  } else {
    document.getElementById('ltb-delete').classList.remove('active');
    if (selectedLayer) deselect();
  }
};

window.ltoDeleteSelected = function() {
  if (!selectedLayer) return;
  deletePolygon(selectedLayer);
};

window.ltoDownload = function() {
  var features = [];
  collectAllPolygons(function(poly) {
    if (!poly._ltoSetup) return;
    try {
      if (!poly.getLatLngs || !poly.getLatLngs().length) return;
      var gj = poly.toGeoJSON();
      if (gj && gj.geometry) {
        features.push({type:'Feature', geometry:gj.geometry, properties:poly.feature?.properties||{}});
      }
    } catch(e) { console.warn('SKIP: export error', e); }
  });
  var blob = new Blob([JSON.stringify({type:'FeatureCollection', features:features}, null, 2)], {type:'application/geo+json'});
  var url = URL.createObjectURL(blob);
  var link = document.getElementById('ltb-dl-link');
  link.href = url; link.download = 'poligonos_editados.geojson'; link.click();
  URL.revokeObjectURL(url);
};

window.ltoDiagnose = function() {
  var total = 0;
  var setup = 0;
  collectAllPolygons(function(poly) {
    total++;
    if (poly._ltoSetup) setup++;
  });
  var msg = 'En mapa: total=' + total + ' setup=' + setup + ' directas=' + (map._layers ? Object.keys(map._layers).length : '?');
  console.log('[LTO] Diagnostico:', {total: total, setup: setup});
  alert(msg);
};

window.ltoSaveToGpkg = function() {
  var features = [];
  collectAllPolygons(function(poly) {
    if (!poly._ltoSetup) return;
    try {
      if (!poly.getLatLngs || !poly.getLatLngs().length) return;
      var gj = poly.toGeoJSON();
      if (gj && gj.geometry) {
        features.push({type:'Feature', geometry:gj.geometry, properties:poly.feature?.properties||{}});
      }
    } catch(e) { console.warn('SKIP: export error', e); }
  });
  console.log('[LTO] ltoSaveToGpkg: features colectados =', features.length);
  if (features.length === 0) return;
  var geojson = {type:'FeatureCollection', features:features};
  var ltoBtn = document.getElementById('ltb-save');
  if (ltoBtn) { ltoBtn.textContent = '...'; ltoBtn.disabled = true; }
  fetch('___SAVE_API_URL___', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({geojson: JSON.stringify(geojson), archivo_gpkg: '___ARCHIVO_GPKG___'}),
  }).then(function(r){ return r.json(); }).then(function(data){
    if (data.status === 'ok') {
      alert('Guardado exitoso. Presiona "Limpiar cach\u00e9" en el panel izquierdo.');
    } else {
      alert('Error al guardar: ' + (data.message || '?'));
    }
  }).catch(function(e){
    alert('Error de conexi\u00f3n: ' + e.message + '. Aseg\u00farate que el servidor local en puerto 8765 est\u00e9 activo.');
  }).finally(function(){
    if (ltoBtn) { ltoBtn.textContent = 'Save'; ltoBtn.disabled = false; }
  });
};

// Map setup
try {
  function collectAllPolygons(fn) {
    map.eachLayer(function(layer) { recurseLayers(layer, fn); });
  }

  function recurseLayers(group, fn) {
    if (!group || !group.getLayers) return;
    group.getLayers().forEach(function(l) {
      if (l instanceof L.Polygon) { fn(l); }
      else if (l.getLayers) { recurseLayers(l, fn); }
    });
  }

  function setupPolygon(poly) {
    if (poly._ltoSetup) return;
    if (poly._ltoLocked) return;
    poly._ltoSetup = true;
    poly._origStyle = {
      fillColor: poly.options.fillColor || origStyle.fillColor,
      color: poly.options.color || origStyle.color,
      weight: poly.options.weight || origStyle.weight,
      fillOpacity: poly.options.fillOpacity || origStyle.fillOpacity,
    };
    poly.on('click', function(e) {
      L.DomEvent.stopPropagation(e);
      if (deleteMode) {
        deletePolygon(poly);
      } else {
        selectLayer(poly);
      }
    });
  }

  function deselect() {
    if (!selectedLayer) return;
    try { if (selectedLayer.editor) selectedLayer.disableEdit(); } catch(e) {}
    selectedLayer.setStyle(selectedLayer._origStyle || origStyle);
    selectedLayer = null;
    document.getElementById('ltb-edit').classList.remove('active');
    editMode = false;
  }

  function selectLayer(layer) {
    if (layer === selectedLayer) return;
    deselect();
    selectedLayer = layer;
    layer.setStyle(hiStyle);
    if (editableReady && editMode) {
      try { layer.enableEdit(); } catch(e) {}
      document.getElementById('ltb-edit').classList.add('active');
    }
  }

  collectAllPolygons(function(poly) {
    if (!poly._ltoSetup) setupPolygon(poly);
  });

  map.on('click', function(e) {
    console.log('LTO: map click, drawing:', editableReady && map.editTools.drawing());
    if (selectedLayer && !editMode && !(editableReady && map.editTools.drawing())) deselect();
  });

  if (editableReady) {
    map.on('editable:drawing:start', function() { console.log('LTO: drawing:start'); });
    map.on('editable:drawing:click', function() { console.log('LTO: drawing:click'); });
    map.on('editable:drawing:end', function() { console.log('LTO: drawing:end'); });
    map.on('editable:drawing:cancel', function() { console.log('LTO: drawing:cancel'); });
    map.on('editable:created', function(e) {
      var layer = e.layer;
      if (layer instanceof L.Polygon) {
        if (!DRAW_GROUP) { DRAW_GROUP = L.featureGroup().addTo(map); }
        DRAW_GROUP.addLayer(layer);
        setupPolygon(layer);
        setTimeout(function() { selectLayer(layer); }, 0);
      }
    });
  }
} catch(e) { console.warn('LTO: map setup error', e); }

document.addEventListener('keydown',function(e){
  if ((e.key==='Delete'||e.key==='Del') && selectedLayer) window.ltoDeleteSelected();
});

})();
</script>"""
    js = js.replace(_GPKG_PH, archivo_gpkg.replace("\\", "/")).replace(_API_URL_PH, _save_api_url)
    return tool_html + css + js


def _construir_mapa(gdf, archivo_gpkg: str = "") -> str:
    """Construye mapa Folium, inyecta monkey-patch + toolbar."""
    gdf = gdf.copy()
    gdf["geometry"] = gdf["geometry"].apply(force_2d)
    cols = [c for c in gdf.columns if c != gdf.geometry.name]

    mapa = folium.Map(
        location=[MAPA_CENTRO_LAT, MAPA_CENTRO_LON],
        zoom_start=MAPA_ZOOM_INICIO,
        tiles=MAPA_TILES,
        control_scale=True,
    )

    folium.TileLayer(
        tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
        attr="Esri",
        name="Satélite (Esri)",
        overlay=False,
        control=True,
    ).add_to(mapa)

    try:
        gdf_municipio = cargar_municipio()
        if not gdf_municipio.empty:
            agregar_capa_poligonos(
                mapa=mapa, gdf=gdf_municipio,
                nombre_capa="Municipio de Comayagua",
                color_borde="#3498db", color_relleno="#3498db",
                opacidad_relleno=0.05, opacidad_borde=0.9,
                peso_borde=2.5,
                columnas_popup=["NOMBRE", "superf_ha", "Area_Km2"],
                mostrar_tooltip=False, resaltar_hover=False,
                locked=True,
            )
    except Exception:
        pass

    fg = folium.FeatureGroup(name="Polígonos segmentados", show=True)
    folium.GeoJson(
        gdf,
        style_function=lambda x: {
            "fillColor": "#3498db", "color": "#2980b9",
            "weight": 2, "fillOpacity": 0.3,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=cols[:5], aliases=[f"{c}:" for c in cols[:5]],
        ) if cols else None,
    ).add_to(fg)
    fg.add_to(mapa)

    folium.LayerControl(position="topright", collapsed=False).add_to(mapa)
    Fullscreen(position="topleft", title="Pantalla completa",
               title_cancel="Salir de pantalla completa").add_to(mapa)

    html = mapa.get_root().render()

    # 1) Encontrar var map_xxx = L.map(...) e inyectar window.__folium_map
    m = re.search(r'var (\w+)\s*=\s*L\.map\(', html)
    if not m:
        return html
    map_var = m.group(1)

    # Buscar el final de la sentencia L.map() con contador de paréntesis
    start = m.start()
    depth = 0
    in_str = False
    str_ch = None
    end = start
    for i in range(start, len(html)):
        c = html[i]
        if in_str:
            if c == str_ch and html[i-1] != '\\':
                in_str = False
        elif c in '"\'':
            in_str = True
            str_ch = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                end = i + 1
                while end < len(html) and html[end] in ' \n\r\t':
                    end += 1
                if end < len(html) and html[end] == ';':
                    end += 1
                break

    ref = f'\nwindow.__folium_map = {map_var};'
    html = html[:end] + ref + html[end:]

    # 2) Toolbar + Leaflet.Editable al final (después de Folium scripts)
    inject = _build_toolbar_js(archivo_gpkg)
    parts = html.split("</body>", 1)
    if len(parts) == 2:
        folium_scripts = parts[1].rsplit("</html>", 1)[0]
        html = parts[0] + folium_scripts + inject + "\n</body>\n</html>"
    else:
        html = html.replace("</body>", inject + "\n</body>")

    return html


_VERSION_HASH = hashlib.md5(
    inspect.getsource(_construir_mapa).encode()
    + inspect.getsource(_build_toolbar_js).encode()
).hexdigest()


@st.cache_data(show_spinner=False)
def _html_cacheado(archivo_gpkg: str, code_hash: str) -> tuple[str, int]:
    import geopandas as gpd
    import logging
    _log = logging.getLogger(__name__)
    try:
        gdf = gpd.read_file(archivo_gpkg, layer="parcelas_vigentes")
    except Exception:
        gdf = gpd.read_file(archivo_gpkg)
    _log.info("Mapa carga %d features de %s", len(gdf), archivo_gpkg)
    html = _construir_mapa(gdf, archivo_gpkg)
    return html, len(gdf)


def limpiar_cache() -> None:
    _html_cacheado.clear()


def _on_run_model():
    import streamlit as st
    st.session_state.mostrar_confirmacion = True


def render_mapa_segmentacion(
    gdf,
    nombre_capa: str | None = None,
    archivo_gpkg: str | None = None,
) -> dict:
    resultado: dict = {"selected_id": None, "selected_props": None}
    es_externo = gdf is not None and not gdf.empty

    if es_externo and archivo_gpkg:
        html, n_poly = _html_cacheado(archivo_gpkg, _VERSION_HASH)
        st.iframe(html, height=560)
    else:
        n_poly = 0
        if gdf is not None and gdf.empty:
            st.info("La capa seleccionada no contiene polígonos.", icon="📭")
        else:
            st.info("Selecciona una capa de segmentación en el panel izquierdo.", icon="📁")

    label_capa = nombre_capa or "Capa de segmentación"
    col_info, col_btn = st.columns([3, 1], vertical_alignment="center")
    st.markdown(
        "<style>div[data-testid='stHorizontalBlock'] > div:nth-child(2) button { transform:translateY(8px) }</style>",
        unsafe_allow_html=True,
    )
    with col_info:
        if es_externo:
            st.markdown(
                f"""
                <div style='background:#1a1d23; border:1px solid #2d3139; border-radius:6px;
                            padding:.5rem .9rem; margin:.5rem 0; font-size:.85rem;
                    display:flex; gap:1.5rem; flex-wrap:wrap;'>
                    <span>📁 <b>{label_capa}</b></span>
                    <span>🗺️ Polígonos: <b>{n_poly}</b></span>
                    <span>✏️ Clic en polígono para seleccionar, luego editar/eliminar</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
    with col_btn:
        if es_externo:
            st.button("▶️ Ejecutar segmentación", key="btn_run_model",
                       use_container_width=True, type="primary",
                       on_click=_on_run_model,
                       help="Ejecuta delineate-anything para generar nuevas parcelas segmentadas.")
