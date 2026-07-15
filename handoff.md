# Handoff — Observatorio Maíz, Valle de Comayagua

## Contexto del proyecto

Tesis de Ingeniería en Sistemas: estimación temprana de rendimiento de maíz en el Valle de Comayagua, Honduras, usando imágenes Sentinel-2 gratuitas y el modelo VPM (Vegetation Photosynthesis Model). El sistema tiene cuatro etapas de pipeline:

1. **Segmentación** — SAMGeo para delimitar parcelas agrícolas
2. **Clasificación fenológica** — Score compuesto (Pearson + pendiente de verdor) contra patrones de referencia de maíz grano (rápido/lento)
3. **Estimación VPM** — GPP → NPP → biomasa → rendimiento (qq/ha, qq/parcela)
4. **Observatorio web** — Streamlit con mapa Folium, series temporales EVI/LSWI/GPP, estimaciones por ventana T1/T2/T3/EOS

Unidades de rendimiento en toda la UI: **quintales** (qq/ha y qq/parcela). Nunca toneladas métricas.

---

## Stack tecnológico

| Categoría | Librería |
|---|---|
| UI | streamlit >= 1.45, streamlit-folium >= 0.25 |
| Mapas | folium >= 0.19, shapely >= 2.1 |
| Gráficas | plotly >= 6.0 |
| Geodatos | geopandas >= 1.1, pyogrio, fiona |
| BD | sqlite3 (stdlib), GeoPackage |
| Satélite | openeo (backend CDSE + federado AgERA5) |
| Suavizado | whittaker-eilers >= 0.2 |
| Ajuste curvas | scipy.optimize.least_squares (doble logística) |
| Gestor paquetes | uv (no pip directo) |

---

## Funciones para cargar índices desde BD

### `pipeline/ingesta.py`

- **`cargar_indices_desde_bd(fecha_inicio, fecha_fin, ids_parcelas)`** → `dict{"EVI": DataFrame, "LSWI": DataFrame}` con DatetimeIndex y columnas `id_<N>`. Consulta `series_diarias_vpm`. Lanza `ValueError` si no hay datos.

- **`cargar_clima_desde_bd(fecha_inicio, fecha_fin, ids_parcelas)`** → `dict{"temperature-mean": DataFrame, "solar-radiation-flux": DataFrame}`. Broadcast automático a todas las parcelas. AgERA5 resolución ~11km.

- **`obtener_indices(connection, geojson_openeo, fecha_inicio, fecha_fin, config_cloud_mask, forzar_descarga)`** — Con caché inteligente: consulta cobertura STAC (`cobertura_sentinel2`) y fechas `consultado=1`; solo descarga gaps de openEO. Persiste con `guardar_indices_crudos()`.

- **`obtener_clima(connection, geojson_openeo, fecha_inicio, fecha_fin, num_parc, forzar_descarga)`** — Análogo para clima AgERA5 con detección de gaps.

- **`obtener_datacube_indices_crudo(connection, geojson_openeo, fecha_inicio, fecha_fin, config_cloud_mask)`** — Descarga directa openEO (sin caché). Pipeline: máscara SCL dilation → bandas B02/B04/B08/B11 → corrección DN a reflectancia `(x + BOA_OFFSET) / ESCALA` → mask → interpolación temporal → cálculo EVI/LSWI → reducción zonal media.

- **`obtener_datos_climaticos_crudo(...)`** — Descarga AgERA5 directa (temperature-mean, solar-radiation-flux). Broadcast regional.

- **`guardar_indices_crudos(dfs, mode)`** / **`guardar_datos_climaticos(dfs, ids_parcelas, mode)`** / **`guardar_gpp_diario(dfs_gpp, mode)`** — Persisten en `series_diarias_vpm` con upsert. `mode="replace"` borra solo el rango de parcelas/fechas afectadas.

- **`resincronizar_indices_parcelas(...)`** — Descarga completa sin gap detection, para parcelas nuevas.

### `utils/queries.py` (con caché Streamlit)

- **`cargar_parcelas(layer="parcelas_vigentes")`** → `GeoDataFrame` en EPSG:4326.
- **`cargar_lista_parcelas()`** → `list[int]` de id_parcela.
- **`cargar_datos_series(parcela_id)`** → `dict{"raw": {...}, "smoothed": {...}}` con EVI/LSWI. Usa `cargar_indices_desde_bd` + `preprocesar_indices_vpm`.
- **`cargar_ciclos_historicos(anio, temporada, id_parcela)`** → `DataFrame` de `produccion_acumulada_ciclo`.
- **`cargar_predicciones_ciclo(id_ciclo)`** → `DataFrame` de `predicciones_ventana`.
- **`cargar_indices_suavizados(id_ciclo)`** → `DataFrame` de `indices_suavizados`.
- **`cargar_indices_crudos(id_parcela, fecha_inicio, fecha_fin)`** → `DataFrame` de `series_diarias_vpm`.
- **`cargar_extrapolacion_prediccion(id_prediccion)`** → `dict{"EVI": Series, "LSWI": Series}` de `series_extrapoladas_ventana`.
- **`cargar_ciclos_no_finalizados(temporada)`** → Ciclos `candidato`/`activo` con scores de clasificación.
- **`cargar_municipio()`** → `GeoDataFrame` del municipio desde GeoJSON estático.

---

## Función de suavizado (Whittaker-Eilers)

### `utils/aplicar_whittaker.py`

```python
aplicar_whittaker_series(diccionario_dfs, lambda_param=10000.0, orden=2) -> dict
```

- Recibe `dict[str, pd.DataFrame]` con DatetimeIndex diario (NaN en días sin datos).
- Procesa parcela por parcela (columna por columna).
- Pesos: 0 para NaN, 1 para valores válidos.
- Si una parcela tiene menos de `orden+1` observaciones válidas, fallback a interpolación lineal.
- Retorna mismo dict con series suavizadas y sin NaN.

### Parámetros típicos

| Contexto | lambda_param | orden |
|---|---|---|
| Pipeline VPM (`modulo_vpm.py`) | 4000.0 | 2 |
| Visualización independiente | 10000.0 | 2 |

### `pipeline/modulo_vpm.py`

**`preprocesar_indices_vpm(dfs_vpm_crudos, lambda_param=4000.0, lswi_max=None)`** → Flujo completo:
1. Filtra valores atípicos (EVI/LSWI fuera de [-1, 1])
2. Reindexa a diario (NaN en días sin dato)
3. Suaviza con `aplicar_whittaker_series(lambda=lambda_param)`
4. Calcula W_scalar = (1 + LSWI) / (1 + LSWI_max)
5. Calcula FPAR = 1.0 * EVI
6. Retorna `{"EVI", "LSWI", "FPAR", "W_scalar"}`

---

## Esquema de Base de Datos (`pipeline.gpkg`)

### Capas vectoriales (GeoPackage, escritas con geopandas/pyogrio)

| Clave LAYERS_GPKG | Nombre real | Estado | Columnas |
|---|---|---|---|
| `parcelas` | `parcelas_vigentes` | ✅ | id_parcela INTEGER, area_ha REAL, area_m2 REAL, geometry (EPSG:32616) |
| `ciclos` | `ciclos` | ⏳ | — |
| `gpp_diario` | `gpp_diario` | ⏳ | — |
| `rendimiento` | `rendimiento` | ⏳ | — |
| `serie_evi` | `serie_evi` | ⏳ | — |
| `serie_lswi` | `serie_lswi` | ⏳ | — |

### Tablas SQLite (creadas por `_crear_tablas_sql()` en `utils/db.py`)

**`series_diarias_vpm`** — Tabla central de series temporales
| Columna | Tipo | Descripción |
|---|---|---|
| id_parcela | INTEGER | FK → parcelas_vigentes |
| fecha | DATE | |
| evi_crudo | REAL | EVI crudo de Sentinel-2 |
| lswi_crudo | REAL | LSWI crudo de Sentinel-2 |
| temperatura_diaria_promedio | REAL | °C (AgERA5) |
| radiacion_total_promedio | REAL | J/m²/día (AgERA5) |
| gpp_diario | REAL | g C/m²/día (VPM) |
| consultado | INTEGER | 1 = ya enviado a openEO |
| PK | (id_parcela, fecha) | |

**`lswi_maximo`** — LSWI máximo histórico por parcela/temporada
| Columna | Tipo |
|---|---|
| id_parcela | INTEGER |
| lswi_max | REAL |
| temporada | TEXT |
| PK | (id_parcela, temporada) |

**`produccion_acumulada_ciclo`** — Ciclos de producción y rendimiento
| Columna | Tipo |
|---|---|
| id_ciclo | INTEGER PK AUTOINCREMENT |
| id_parcela | INTEGER NOT NULL |
| temporada | TEXT (primera/postrera) |
| lswi_max | REAL |
| lswi_max_efectivo | REAL |
| fecha_inicio | DATE (valle anterior) |
| sos | DATE (start of season) |
| t1, t2, t3 | DATE |
| eos | DATE (end of season) |
| fecha_fin | DATE (valle posterior) |
| rendimiento | REAL (qq/ha) |
| produccion_total | REAL (qq) |
| clasificacion_final | TEXT (Maíz, Maíz - baja probabilidad, Otro, Incierto) |
| estado_ciclo | TEXT (candidato/activo/finalizado) |

**`indices_suavizados`** — EVI/LSWI post-Whittaker por ciclo
| PK (id_ciclo, fecha) | evi, lswi |

**`predicciones_ventana`** — Predicciones por ventana T1/T2/T3/EOS
| PK (id_prediccion AUTOINCREMENT) | UNIQUE (id_ciclo, ventana) |
| Columnas: ventana, fecha_ventana, lswi_max_efectivo_usado, gpp_acumulado, npp_acumulado, rendimiento_estimado_qq_ha, rendimiento_estimado_qq_parcela, score_pearson, score_magnitud_pendiente, score_compuesto, cultivo_predicho, fecha_congelamiento |

**`series_extrapoladas_ventana`** — Tramo extrapolado de cada predicción
| PK (id_prediccion, fecha) | evi_extrapolado, lswi_extrapolado |

**`climatologia_diaria`** — Climatología AgERA5 por día del año
| PK (id_region, variable, dia_anio) | valor_climatologico, anio_min_incluido, anio_max_incluido |

**`patron_referencia_fenologico`** — Patrones de EVI para clasificación
| PK (id_patron AUTOINCREMENT) | UNIQUE (subtipo, dia_post_sos, version) |
| Columnas: subtipo (grano_rapido/grano_lento), dia_post_sos, evi_promedio, evi_desviacion, mediana_pendiente_verdeo, n_muestras, ids_parcelas_usadas |

**`cobertura_sentinel2`** — Fechas de adquisición S2 (STAC)
| PK (id_cobertura AUTOINCREMENT) | fecha |

---

## Decisiones del proyecto (reglas no negociables)

### Arquitectura
1. **Separación estricta**: páginas solo ensamblan UI, lógica en `utils/`, renderizado en `components/`.
2. **Conexión centralizada**: toda conexión SQLite vive en `utils/conexionDB.py`. `get_connection()` / `get_connection_raw()`.
3. **Constantes en `config.py`**: ningún valor hardcodeado en páginas o componentes.
4. **Funciones puras en `utils/`**: sin llamadas a `st.*` excepto decoradores `@st.cache_data`.
5. **Sin `if __name__ == "__main__"`** en páginas ni en `app.py`. Solo en `utils/` para pruebas desde terminal.

### Navegación Streamlit
- `st.navigation` + `st.Page` (método oficial). `app.py` es entrypoint único, aplica `set_page_config` e `inyectar_estilos()`.
- Páginas individuales NO llaman a `set_page_config` ni `inyectar_estilos()`.

### Datos geoespaciales
- **CRS métrico**: EPSG:32616 (UTM 16N) para cálculos de área y distancia.
- **CRS geográfico**: EPSG:4326 solo para Folium.
- **Folium sobre leafmap**: `st_folium()` con key dinámico por ciclo/ventana/modo.
- **Municipio**: contorno azul `#3498db`, tooltip desactivado. Parcelas: verde `#2ecc71` (primera), naranja `#e67e22` (postrera).

### Pipeline VPM
1. **Ingesta con caché**: STAC determina fechas de adquisición S2 reales. `consultado=1` marca lo ya enviado a openEO. Solo se descargan gaps.
2. **Suavizado sin bfill/ffill**: NaN por nubes se preservan para Whittaker. No rellenar fuera del período vegetativo.
3. **Whittaker**: `lambda=4000` para pipeline VPM, `lambda=10000` para visualización. Las funciones están en `utils/aplicar_whittaker.py`.
4. **LSWI_max**: se calcula del máximo de la serie suavizada o se pasa explícito desde `lswi_maximo`.
5. **GPP**: VPM diario con parámetros: epsilon_0=1.6 (C4), t_min=10°C, t_opt=30°C, t_max=45°C, par_fraction=0.48.
6. **Rendimiento**: NPP = GPP * CUE (0.55). Biomasa = NPP / fraccion_carbono (0.45). Yield = biomasa * harvest_index (0.48) * 0.01 → t/ha → qq/ha (* 22.0458).
7. **Detección SOS**: método `seasonal_amplitude` (TIMESAT). Factor=0.2. Opcional: `ventana_busqueda` y `ventana_sos` para restringir por calendario.

### Clasificación fenológica (`pipeline/modulo_clasificacion.py`)
- Compara EVI observado contra dos patrones: `grano_rapido` y `grano_lento`.
- Score compuesto = max(0, r_pearson) * ratio_pendiente * 100.
- Etiquetas: >= 70 → "Maíz", >= 30 → "Maíz - baja probabilidad", < 30 → "Otro".
- Persiste en `predicciones_ventana.score_compuesto` y `produccion_acumulada_ciclo.clasificacion_final`.

### Predicción por ventana (`pipeline/modulo_predictivo.py`)
- **T1** (30d): ajuste doble logística con 4 parámetros (vmin, vmax, S, mS), A fijo en 0.7*duración, mA = 0.85*mS. Extrapolación con decaimiento exponencial (τ=10d).
- **T2** (60d): ajuste T1 con límites relajados en S.
- **T3** (90d): ajuste doble logística completa (6 parámetros: vmin, vmax, S, mS, A, mA).
- **EOS** (130d): usa la serie completa observada; sin extrapolación.
- Clima futuro se completa con climatología diaria (promedio histórico AgERA5).

### Worker diario (`pipeline/worker.py`)
- Ejecutable como `python -m pipeline.worker [--fecha YYYY-MM-DD]`.
- Flujo: ingesta STAC/AgERA5 → detectar ciclos pendientes → promover candidatos (3 observaciones consecutivas sobre umbral) → preprocesar ciclos activos → ejecutar ventanas T1/T2/T3/EOS.
- Configurable via `worker_config.json` (hora, temporada activa, factor SOS).
- Se registra en Windows Task Scheduler automáticamente.

### Orquestación (`pipeline/flujos_trabajo.py`)
Cuatro flujos:
1. `ejecutar_pipeline_completo` — end-to-end con caché BD + gaps openEO.
2. `ejecutar_pipeline_desde_bd` — solo BD, sin conexión openEO.
3. `calcular_rendimiento_desde_indices` — DataFrames ya en memoria.
4. `ejecutar_prediccion_ventana` — predicción T1/T2/T3/EOS para un ciclo histórico.

### Misceláneo
- **Estado vacío**: siempre `st.warning()` descriptivo, nunca fallo silencioso.
- **Caché viejos**: botón "🔄 Limpiar caché" en sidebar de Parcelas, o reiniciar Streamlit.
- **Código en español técnico**: variables, funciones, comentarios, docstrings y UI.
- **Seed histórico**: `python main.py` (correr con Streamlit detenido).
- **Para levantar**: `.venv\Scripts\python.exe -m streamlit run app.py`
