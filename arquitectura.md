# Arquitectura del Proyecto

## Visión General

Sistema de monitoreo agrícola para la estimación de rendimiento de maíz en el Valle de Comayagua, Honduras. Integra **segmentación de parcelas**, **clasificación de cultivos** (maíz vs. otros) y **estimación de rendimiento** usando imágenes satelitales Sentinel-2 y el modelo VPM (Vegetation Photosynthesis Model). La plataforma se despliega como una aplicación **Streamlit** con interfaz web interactiva y una **CLI** para operación por terminal, con un worker diario automatizado.

---

## Stack Tecnológico

| Componente          | Tecnología                          |
|---------------------|-------------------------------------|
| Lenguaje            | Python >=3.12                       |
| Dashboard           | Streamlit + Folium                  |
| Datos satelitales   | openEO (CDSE Copernicus Data Space) |
| Datos climáticos    | openEO federado + AgERA5            |
| DB espacial         | GeoPackage (SQLite + SpatiaLite)    |
| Procesamiento       | Pandas, NumPy, SciPy, scikit-learn  |
| Suavizado           | Whittaker-Eilers                     |
| Segmentación        | Delineate-Anything (YOLO)           |
| Modelos             | Random Forest, Ledoit-Wolf          |
| Visualización       | Folium, Plotly, hvPlot, HoloViews   |
| Automatización      | Windows Task Scheduler              |
| Tests               | pytest                              |

---

## Estructura de Directorios

```
├── app.py                          # Entrypoint Streamlit (st.navigation)
├── CLI.py                          # Menú interactivo por terminal
├── main.py                         # Script simple de seeding
├── config.py                       # Constantes globales del proyecto
├── pyproject.toml                  # Dependencias del proyecto
├── worker_config.json              # Configuración del worker diario
│
├── pipeline/                       # Núcleo del pipeline de procesamiento
│   ├── flujos_trabajo.py           # Orquestador de predicción de rendimiento
│   ├── ingesta.py                  # Ingesta satelital y climática (EVI/LSWI + AgERA5)
│   ├── modulo_vpm.py               # Modelo VPM (preprocesamiento, GPP, biomasa)
│   ├── modulo_fenologico.py        # Detección fenológica (SOS, segmentación de ciclos)
│   ├── modulo_clasificacion.py     # Clasificación de cultivos (maíz vs otro)
│   ├── modulo_predictivo.py        # Predicción por ventanas (curva doble logística)
│   ├── modulo_historico.py         # Procesamiento histórico multianual
│   ├── modulo_parcelas.py          # Gestión de parcelas + Delineate-Anything
│   ├── worker.py                   # Worker diario automatizado
│   └── openeo_catalogo.py          # Consulta al catálogo openEO/STAC
│
├── pages/                          # Páginas de la aplicación Streamlit
│   ├── 1_Parcelas.py               # Observatorio (mapa principal)
│   ├── 2_Series_Temporales.py      # Series temporales EVI/LSWI
│   ├── 3_Estimacion.py             # Estimación de rendimiento
│   ├── 4_Resumen_Valle.py          # Resumen agregado del valle
│   ├── 5_Acerca_de.py              # Acerca del proyecto
│   ├── 6_Analisis_Historico.py     # Análisis histórico
│   ├── 7_Clasificacion_Parcelas.py # Clasificación de parcelas
│   └── 8_Segmentacion_Parcelas.py  # Segmentación de parcelas
│
├── components/                     # Componentes reutilizables de la UI
│   ├── estilos.py                  # Estilos CSS globales
│   ├── sidebar_filtros.py          # Filtros del sidebar
│   ├── mapa_parcelas.py            # Componente de mapa de parcelas
│   ├── mapa_segmentacion.py        # Componente de mapa de segmentación
│   ├── graficas_series.py          # Gráficas de series temporales
│   ├── graficas_estimacion.py      # Gráficas de estimación
│   └── graficas_resumen.py         # Gráficas de resumen
│
├── utils/                          # Utilidades de soporte
│   ├── conexionDB.py               # Conexión a BD GeoPackage
│   ├── db.py                       # Operaciones de base de datos (seeding)
│   ├── queries.py                  # Consultas SQL predefinidas
│   ├── aplicar_whittaker.py        # Suavizado Whittaker-Eilers
│   ├── dict_a_dataframe.py         # Conversión de dict openEO a DataFrames
│   ├── capas_folium.py             # Capas para mapas Folium
│   ├── cobertura_sentinel2.py      # Cobertura STAC Sentinel-2
│   ├── descargar_datacube.py       # Descarga de datacubes
│   ├── visualizar_datacube.py      # Visualización de datacubes
│   ├── detectarVectores.py         # Detección de vectores
│   ├── gpkg_layer.py               # Manejo de capas GeoPackage
│   ├── graficas.py                 # Funciones de gráficos
│   ├── kml_a_geojson.py            # Conversión KML a GeoJSON
│   ├── reporte_cobertura.py        # Reporte de cobertura BD vs servidor
│   ├── save_api.py                 # API de guardado
│   └── funciones_aux.py            # Funciones auxiliares varias
│
├── delineate_anything/             # Herramienta de segmentación (sub-proyecto)
│   ├── delineate.py                # Script principal de segmentación
│   ├── simplify.py                 # Simplificación de geometrías
│   ├── batch_sample.yaml           # Configuración batch
│   ├── methods/                    # Métodos de segmentación
│   └── data/delineated/            # Resultados de segmentación
│
├── clasificadores/                 # Modelos de clasificación pre-entrenados
│   ├── rf_maiz_comayagua_puro_30p.pkl
│   └── rf_maiz_comayagua_puro_offset_30p.pkl
│
├── data/                           # Datos geoespaciales
│   ├── pipeline.gpkg               # BD principal (GeoPackage)
│   ├── pipeline_pruebas.gpkg       # BD de pruebas
│   ├── PoligonosMaizPlayitas.geojson
│   ├── ParcelasNoMaiz.geojson
│   └── Datos Referencia/           # Datos de referencia
│
├── notebooks/                      # Notebooks Jupyter de experimentación
│   ├── clasificacion.ipynb
│   ├── ClasificadorFenologicoHidrologico.ipynb
│   ├── EntrenamientoEuocrops.ipynb
│   └── ...
│
├── eval_scripts/                   # Scripts de evaluación
│   ├── eval_clasificacion.py
│   └── eval_cobertura.js
│
├── static/                         # Assets estáticos (GeoJSON, etc.)
├── logs/                           # Logs del worker
├── tests/                          # Tests unitarios (pytest)
├── Planes/                         # Documentación de planificación
│
├── .env                            # Variables de entorno
└── .gitignore
```

---

## Flujo de Datos del Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                   1. SEGMENTACIÓN DE PARCELAS                           │
│  GeoJSON de entrada → Delineate-Anything (YOLO) → Geometrias .gpkg     │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   2. INGESTA DE DATOS SATELITALES                       │
│  openEO CDSE → Sentinel-2 L2A → Máscara SCL → Interpolación lineal     │
│  → Cálculo EVI/LSWI → Reducción zonal → series_diarias_vpm             │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   3. INGESTA DE DATOS CLIMÁTICOS                        │
│  openEO Federado → AgERA5 → Temperatura + Radiación → series_diarias   │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   4. PREPROCESAMIENTO VPM                               │
│  Filtro outliers → Reindexado diario → Whittaker-Eilers → FPAR         │
│  → W_scalar (estrés hídrico) → índices suavizados                      │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   5. FENOLOGÍA (SOS)                                    │
│  Detectar inicio de temporada (SOS) por parcela → TIMESAT seasonal     │
│  amplitude → Persistir en produccion_acumulada_ciclo                    │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   6. PREDICCIÓN POR VENTANAS (T1/T2/T3/EOS)             │
│  Curva doble logística → Extrapolación sintética → Climatología        │
│  → GPP (VPM) → NPP → Biomasa → Rendimiento (qq/ha)                    │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   7. CLASIFICACIÓN DE CULTIVO                           │
│  Correlación con patrón fenológico + Mahalanobis → Score compuesto     │
│  → Maíz / Maíz-baja / Otro                                             │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                   8. VISUALIZACIÓN (Streamlit)                          │
│  Mapas Folium + Series temporales + Tablas de rendimiento + Resúmenes  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Componentes del Pipeline

### 1. `pipeline/ingesta.py` — Ingesta de datos

- **`obtener_indices()`**: Descarga EVI/LSWI desde openEO CDSE con caché en BD. Detecta gaps usando STAC (fechas de adquisición reales) y solo descarga fechas faltantes.
- **`obtener_clima()`**: Descarga temperatura y radiación solar desde AgERA5 (openEO federado), con misma lógica de caché.
- **`obtener_datacube_indices_crudo()`**: Construye el cubo Sentinel-2 con máscara de nubes morfológica (SCL dilation mask), interpola píxeles enmascarados, calcula EVI y LSWI, y reduce por zona.
- **`guardar_indices_crudos()` / `guardar_datos_climaticos()`**: Persisten en `series_diarias_vpm`.
- **`cargar_indices_desde_bd()` / `cargar_clima_desde_bd()`**: Lectura desde BD en formato compatible.

### 2. `pipeline/modulo_vpm.py` — Modelo VPM

- **`preprocesar_indices_vpm()`**: Filtra outliers (EVI/LSWI fuera de [-1, 1]), reindexa a diario, aplica Whittaker-Eilers, calcula FPAR (= EVI) y W_scalar (estrés hídrico).
- **`calcular_gpp_vpm()`**: Calcula GPP diario = ε₀ × T_scalar × W_scalar × FPAR × PAR. Incluye T_scalar (escalar de temperatura con función de respuesta cuadrática).
- **`calcular_biomasa_y_rendimiento()`**: NPP = GPP × CUE; Biomasa = NPP / fracción_carbono; Rendimiento = Biomasa_acumulada × Harvest_Index.

### 3. `pipeline/modulo_fenologico.py` — Fenología

- **`detectar_sos()`**: Implementa el método `seasonal_amplitude` de TIMESAT 3.3: encuentra pico, base, calcula umbral = base + factor × amplitud y detecta el cruce.
- **`segmentar_ciclos()`**: Segmenta series multianuales en ciclos individuales usando detección de valles (scipy `find_peaks`).
- **`detectar_sos_por_parcela()`**: Ejecuta SOS para todas las parcelas en lote.

### 4. `pipeline/modulo_predictivo.py` — Predicción

- **`ajustar_curva_doble_logistica()`**: Ajusta curva doble logística (TIMESAT) sobre EVI/LSWI observado, con estrategias diferenciadas por ventana T1/T2/T3.
- **`extender_serie_con_curva_parametrica()`**: Extrapola la serie más allá de lo observado usando la curva ajustada.
- **`construir_climatologia_diaria()`**: Construye climatología histórica (promedio por día del año) desde AgERA5.
- **`ejecutar_prediccion_ventana()`**: Orquesta la cadena completa: extensión sintética → GPP → NPP → rendimiento para una ventana específica.

### 5. `pipeline/modulo_clasificacion.py` — Clasificación

- **Clasificador fenológico (Pearson + pendiente)**: Compara la serie EVI observada contra patrones de referencia ("grano_rapido", "grano_lento") usando correlación de Pearson y pendiente de verdor.
- **Clasificador por tipicidad (Mahalanobis)**: Extrae 6 métricas fenológicas (max EVI, velocidad, aceleración, ratio EVI/LSWI, correlación interna) y evalúa distancia de Mahalanobis contra un perfil de referencia entrenado con Ledoit-Wolf.
- **`clasificar_parcela_actual()`**: Función principal que evalúa una parcela contra ambos patrones y devuelve el mejor score.

### 6. `pipeline/flujos_trabajo.py` — Orquestador

- **`ejecutar_pipeline_completo()`**: Flujo end-to-end (BD + openEO para gaps).
- **`ejecutar_pipeline_desde_bd()`**: Solo desde BD (sin openEO).
- **`calcular_rendimiento_desde_indices()`**: Núcleo puro desde DataFrames en memoria.
- **`ejecutar_prediccion_ventana()`**: Predicción por ventana para un ciclo específico.
- **`recalcular_en_memoria()`**: Recálculo con SOS/EOS personalizados sin persistir.

### 7. `pipeline/worker.py` — Worker Diario

- Automatización diaria que: consulta nuevas adquisiciones Sentinel-2, ingesta datos faltantes, ejecuta SOS, procesa ventanas T1/T2/T3/EOS, y persiste predicciones.
- Soporta modo simulación con fecha personalizada.
- Se integra con Windows Task Scheduler.

### 8. `pipeline/modulo_historico.py` — Histórico

- **`seed_series_historicas()`**: Ingesta y procesa datos multianuales completos (índices + clima + segmentación + SOS + predicciones).
- **`seed_historico_offline()`**: Versión offline que solo usa BD local (sin openEO).

### 9. `pipeline/modulo_parcelas.py` — Parcelas

- **`ejecutar_delineate_anything_local()`**: Lanza Delineate-Anything en entorno Conda para segmentación automática de parcelas.

---

## Esquema de la Base de Datos (GeoPackage)

### Tablas principales en `pipeline.gpkg`:

| Tabla                          | Propósito                                            |
|--------------------------------|------------------------------------------------------|
| `parcelas_vigentes`            | Geometrías de parcelas con id_parcela y área         |
| `series_diarias_vpm`           | Series diarias: EVI crudo, LSWI crudo, temperatura, radiación, GPP |
| `produccion_acumulada_ciclo`   | Ciclos de producción: SOS, T1, T2, T3, EOS, rendimiento, clasificación |
| `predicciones_ventana`         | Predicciones congeladas por ventana (T1/T2/T3/EOS)   |
| `indices_suavizados`           | EVI/LSWI suavizados por Whittaker                    |
| `series_extrapoladas_ventana`  | Tramo extrapolado de series para cada predicción     |
| `gpp_diario`                   | GPP diario calculado                                 |
| `lswi_maximo`                  | LSWI máximo histórico por parcela y temporada        |
| `climatologia_diaria`          | Climatología de radiación y temperatura por día del año |
| `perfil_tipicidad_maiz`        | Perfiles de referencia para clasificación Mahalanobis |
| `patron_referencia_fenologico` | Patrones fenológicos de referencia (grano_rapido/lento) |
| `ciclos`                       | Ciclos de siembra                                    |

---

## Interfaces de Usuario

### 1. Aplicación Streamlit (`app.py`)

Usa `st.navigation` con 4 secciones:

- **Observatorio Regional**: Mapa principal (Folium), series temporales, estimación, resumen del valle.
- **Análisis Histórico**: Visualización de datos multianuales con ajuste manual de límites SOS/EOS.
- **Administración**: Clasificación de parcelas, segmentación (Delineate-Anything).
- **Acerca de**: Información del proyecto.

### 2. CLI (`CLI.py`)

Menú interactivo con 7 secciones numeradas:

1. Gestión de parcelas (seeding, delineación)
2. Ingesta de datos (índices + clima openEO)
3. Motor de predicción (pipeline completo/desde BD/desde memoria)
4. Inspección de BD (tablas, SQL personalizado)
5. Diagnóstico (rutas, integridad, versiones)
6. Módulo fenológico (SOS, segmentación de ciclos)
7. Worker diario (configurar, ejecutar, programar)

---

## Modelo VPM (Vegetation Photosynthesis Model)

El núcleo del pipeline de estimación de rendimiento sigue las ecuaciones:

1. **FPAR** = EVI (Xiao 2004)
2. **W_scalar** = (1 + LSWI) / (1 + LSWI_max)
3. **T_scalar** función cuadrática con T_min=10°C, T_opt=28°C, T_max=48°C
4. **PAR** = SSRD × 0.45 (fracción fotosintética)
5. **ε** = ε₀ × T_scalar × W_scalar (ε₀ = 3.12 g C/MJ para maíz C4)
6. **GPP** = ε × FPAR × PAR
7. **NPP** = GPP × CUE (0.55)
8. **Biomasa** = NPP / 0.45 (fracción de carbono)
9. **Rendimiento** = Biomasa_acumulada × HI × 0.01 (HI = 0.48, t/ha)

---

## Ventanas de Predicción

| Ventana | Días post-SOS | Descripción                                |
|---------|---------------|--------------------------------------------|
| T1      | 30            | Predicción temprana (crecimiento inicial)  |
| T2      | 60            | Predicción intermedia                      |
| T3      | 90            | Predicción avanzada                        |
| EOS     | 130 (fin ciclo) | Predicción final (madurez/cosecha)        |

---

## Ciclos de Siembra

| Ciclo    | Periodo                  | Rendimiento referencia |
|----------|--------------------------|------------------------|
| Primera  | Abril - Julio            | 45.0 qq/ha            |
| Postrera | Agosto - Marzo           | 38.0 qq/ha            |

---

## Flujo del Worker Diario

```
1. Cargar worker_config.json (temporada activa, hora, factor_sos)
2. Conectar a openEO CDSE + Federado
3. Ingesta de nuevas adquisiciones S2 (STAC vs consultado)
4. Ingesta de datos climáticos faltantes
5. Detectar parcelas sin ciclo activo → crear candidatos
6. Promover candidatos a activos (señal persistente)
7. Para cada ciclo activo:
   a. Cargar índices desde BD
   b. Preprocesar (Whittaker)
   c. Detectar/persistir SOS si no existe
   d. Para T1/T2/T3/EOS: ejecutar predicción si fecha_ventana <= hoy
8. Actualizar worker_config (ultima_ejecucion, proxima_ejecucion)
```

---

## Clasificación de Cultivos

### Método 1: Correlación Fenológica
- Compara la curva EVI observada contra patrones de referencia ("grano_rapido", "grano_lento")
- Usa correlación de Pearson (forma) + pendiente de verdor (magnitud)
- Score compuesto = max(0, r) × ratio_pendiente × 100
- Clasifica: Maíz (≥70), Maíz-baja (≥30), Otro (<30)

### Método 2: Distancia de Mahalanobis
- Extrae 6 features fenológicas (max EVI, velocidad, aceleración, ratio EVI/LSWI, correlación)
- Perfil de referencia entrenado con Ledoit-Wolf (estimador shrinkage)
- Evalúa tipicidad respecto al perfil de maíz

---

## Puntos de Entrada

| Comando                           | Propósito                          |
|-----------------------------------|------------------------------------|
| `python app.py` (via streamlit)   | Dashboard web                      |
| `python CLI.py`                   | Menú interactivo terminal          |
| `python main.py`                  | Seeding rápido desde GeoJSON       |
| `python pipeline/worker.py`       | Worker diario                      |
| `python pipeline/worker.py --fecha 2025-08-15` | Worker simulación |
| `python -m pytest tests/ -v`      | Tests unitarios                    |
