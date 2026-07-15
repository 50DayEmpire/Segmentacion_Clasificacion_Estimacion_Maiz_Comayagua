import plotly.graph_objects as go
import ipywidgets as widgets
from ipywidgets import interact
import matplotlib.pyplot as plt
import pandas as pd
from pipeline.ingesta import cargar_indices_desde_bd
from pipeline.modulo_vpm import preprocesar_indices_vpm
from pipeline.modulo_fenologico import segmentar_ciclos, detectar_sos
import geopandas as gpd
import folium


def graficar_comparativa_whittaker_plotly(fecha_inicio, fecha_fin, indice_nombre="EVI", distancia_min_dias=90, prominencia_min=0.15):
    """
    Genera un gráfico interactivo con Plotly e ipywidgets que incluye tooltips 
    dinámicos y líneas verticales que delimitan los segmentos fenológicos.
    """
    # 1. Carga única de datos desde la BD
    dfs_raw = cargar_indices_desde_bd(fecha_inicio, fecha_fin)
    dict_parametros = preprocesar_indices_vpm(dfs_raw)
    
    df_crudo = dfs_raw[indice_nombre]
    df_suave = dict_parametros[indice_nombre]
    parcelas = df_crudo.columns.tolist()

    # 2. Función interna que interact actualizará de forma nativa
    def actualizar_grafico(parcela):
        fig = go.Figure()

        # A. Graficar la serie suavizada diaria (Línea continua)
        fig.add_trace(go.Scatter(
            x=df_suave.index,
            y=df_suave[parcela],
            mode='lines',
            line=dict(color='#1f77b4', width=2),
            name='Serie Diaria Suavizada (Whittaker)',
            hovertemplate=f'<b>Fecha:</b> %{{x|%Y-%m-%d}}<br><b>{indice_nombre} Suave:</b> %{{y:.3f}}<extra></extra>'
        ))

        # B. Graficar los puntos originales de Sentinel-2 (Filtrando NaNs)
        df_puntos = df_crudo[parcela].dropna()
        fig.add_trace(go.Scatter(
            x=df_puntos.index,
            y=df_puntos.values,
            mode='markers',
            marker=dict(color='orange', size=7, line=dict(width=1, color='DarkSlateGrey')),
            name='Adquisiciones Reales S2',
            hovertemplate=f'<b>Fecha Real:</b> %{{x|%Y-%m-%d}}<br><b>{indice_nombre} Crudo:</b> %{{y:.3f}}<extra></extra>'
        ))

        # C. Calcular y graficar los segmentos fenológicos
        segmentos = segmentar_ciclos(df_suave[parcela], distancia_min_dias, prominencia_min)
        etiquetadas = set()

        for inicio, fin in segmentos:
            # Añadir líneas verticales punteadas y anotaciones para cada fecha límite (valles)
            for f in (inicio, fin):
                # Añadir la línea vertical (siempre se dibuja)
                fig.add_vline(
                    x=f, 
                    line_width=1.5, 
                    line_dash="dot", 
                    line_color="red"
                )
                
                # Evitar duplicar el texto si un valle es compartido entre ciclos contiguos
                if f not in etiquetadas:
                    fig.add_annotation(
                        x=f,
                        y=1,               # Posicionado en el tope superior del gráfico
                        yref="paper",      # Referencia relativa al lienzo (0 = abajo, 1 = arriba)
                        text=f.strftime("%Y-%m-%d"),
                        showarrow=False,
                        textangle=-90,     # Rotación vertical de la fecha
                        xanchor="right",
                        yanchor="top",
                        font=dict(color="red", size=9, family="Arial", weight="bold"),
                        bgcolor="rgba(255, 255, 255, 0.7)" # Fondo semitransparente para legibilidad
                    )
                    etiquetadas.add(f)

        # D. Estética del Layout de Plotly
        fig.update_layout(
            title=dict(
                text=f"Segmentación: {indice_nombre} en {parcela}",
                font=dict(size=14, weight='bold')
            ),
            xaxis_title="Fecha",
            yaxis_title=f"{indice_nombre}",
            xaxis=dict(range=[df_suave.index.min(), df_suave.index.max()]),
            template="plotly_white",        # Fondo limpio similar al grid de matplotlib
            hovermode="x unified",          # Cruce unificado: muestra ambos valores al posicionarse sobre la fecha
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            width=950,
            height=450,
            margin=dict(t=80, b=40, l=50, r=40)
        )

        fig.show()

    # 3. Invocar interact de forma limpia
    interact(
        actualizar_grafico, 
        parcela=widgets.Dropdown(
            options=parcelas, 
            value=parcelas[0], 
            description="🌱 Parcela:"
        )
    )

def graficar_comparativa_whittaker(fecha_inicio, fecha_fin, indice_nombre="EVI", distancia_min_dias=90, prominencia_min=0.15):
    """
    Genera un gráfico interactivo con marcadores verticales punteados
    y sus fechas exactas que delimitan los segmentos fenológicos.
    """
    dfs_raw = cargar_indices_desde_bd(fecha_inicio, fecha_fin)
    dict_parametros = preprocesar_indices_vpm(dfs_raw)
    df_crudo = dfs_raw[indice_nombre]
    df_suave = dict_parametros[indice_nombre]
    parcelas = df_crudo.columns.tolist()

    def actualizar_grafico(parcela):
        plt.figure(figsize=(12, 5))
        plt.plot(df_suave.index, df_suave[parcela], color="#1f77b4", linewidth=2, label="Serie Diaria Suavizada (Whittaker)")
        df_puntos = df_crudo[parcela].dropna()
        plt.scatter(df_puntos.index, df_puntos.values, color="orange", s=45, zorder=3, label="Adquisiciones Reales S2")

        segmentos = segmentar_ciclos(df_suave[parcela], distancia_min_dias, prominencia_min)
        ymin, ymax = plt.gca().get_ylim()
        pos_y = ymax - (ymax - ymin) * 0.05
        etiquetadas = set()
        for inicio, fin in segmentos:
            plt.axvline(x=inicio, color="red", linestyle=":", linewidth=1.5, label="Limites de Ciclo" if len(etiquetadas) == 0 else "")
            plt.axvline(x=fin, color="red", linestyle=":", linewidth=1.5)
            for f in (inicio, fin):
                if f not in etiquetadas:
                    plt.text(x=f, y=pos_y, s=f.strftime("%Y-%m-%d"), color="red", fontsize=9, fontweight="bold", rotation=90, va="top", ha="right", backgroundcolor=(1,1,1,0.7))
                    etiquetadas.add(f)

        plt.title(f"Segmentacion: {indice_nombre} en {parcela}", fontsize=12, fontweight="bold")
        plt.xlabel("Fecha", fontsize=10)
        plt.ylabel(f"{indice_nombre}", fontsize=10)
        plt.grid(True, linestyle="--", alpha=0.5)
        plt.legend(loc="upper left")
        plt.xlim(df_suave.index.min(), df_suave.index.max())
        plt.tight_layout()
        plt.show()

    interact(actualizar_grafico, parcela=widgets.Dropdown(options=parcelas, value=parcelas[0], description="Parcela:"))

def graficar_whittaker_sos(fecha_inicio, fecha_fin, indice_nombre="EVI", distancia_min_dias=70, prominencia_min=0.05, factor_sos=0.2):
    """
    Genera un gráfico interactivo con Plotly que delimita los segmentos fenológicos (Rojo)
    y calcula/grafica dinámicamente el Start of Season (SOS) para cada ciclo (Verde).
    """
    # 1. Carga única de datos desde la BD
    dfs_raw = cargar_indices_desde_bd(fecha_inicio, fecha_fin)
    dict_parametros = preprocesar_indices_vpm(dfs_raw)
    
    df_crudo = dfs_raw[indice_nombre]
    df_suave = dict_parametros[indice_nombre]
    parcelas = df_crudo.columns.tolist()

    # 2. Función interna para interact
    def actualizar_grafico(parcela):
        fig = go.Figure()

        # A. Serie suavizada diaria (Línea continua azul)
        fig.add_trace(go.Scatter(
            x=df_suave.index,
            y=df_suave[parcela],
            mode='lines',
            line=dict(color='#1f77b4', width=2),
            name='Serie Diaria Suavizada (Whittaker)',
            hovertemplate=f'<b>Fecha:</b> %{{x|%Y-%m-%d}}<br><b>{indice_nombre} Suave:</b> %{{y:.3f}}<extra></extra>'
        ))

        # B. Puntos originales de Sentinel-2
        df_puntos = df_crudo[parcela].dropna()
        fig.add_trace(go.Scatter(
            x=df_puntos.index,
            y=df_puntos.values,
            mode='markers',
            marker=dict(color='orange', size=7, line=dict(width=1, color='DarkSlateGrey')),
            name='Adquisiciones Reales S2',
            hovertemplate=f'<b>Fecha Real:</b> %{{x|%Y-%m-%d}}<br><b>{indice_nombre} Crudo:</b> %{{y:.3f}}<extra></extra>'
        ))

        # C. Calcular los segmentos fenológicos globales
        segmentos = segmentar_ciclos(df_suave[parcela], distancia_min_dias, prominencia_min)
        
        etiquetadas_valles = set()
        label_limites_agregado = False
        label_sos_agregado = False

        # D. Procesar cada segmento para graficar valles y detectar SOS
        for inicio, fin in segmentos:
            # --- D.1. Graficar límites de ciclos (Líneas rojas) ---
            for f in (inicio, fin):
                fig.add_vline(x=f, line_width=1.5, line_dash="dot", line_color="red")
                
                if f not in etiquetadas_valles:
                    # Forzar una sola etiqueta en la leyenda para las líneas rojas
                    if not label_limites_agregado:
                        fig.add_trace(go.Scatter(x=[f], y=[None], mode='lines', line=dict(color='red', dash='dot'), name='Límites de Ciclo (Valles)', showlegend=True))
                        label_limites_agregado = True

                    fig.add_annotation(
                        x=f, y=1, yref="paper",
                        text=f.strftime("%Y-%m-%d"),
                        showarrow=False, textangle=-90, xanchor="right", yanchor="top",
                        font=dict(color="red", size=9, family="Arial", weight="bold"),
                        bgcolor="rgba(255, 255, 255, 0.7)"
                    )
                    etiquetadas_valles.add(f)

            # --- D.2. Recortar datos del segmento y Ejecutar detectar_sos ---
            mask_segmento = (df_suave.index >= inicio) & (df_suave.index <= fin)
            df_segmento = df_suave.loc[mask_segmento, parcela]

            if not df_segmento.empty:
                resultado_sos = detectar_sos(
                    serie=df_segmento.values,
                    fechas=df_segmento.index,
                    factor=factor_sos,
                    metodo="seasonal_amplitude",
                    ventana_busqueda=(inicio, fin)
                )
                
                fecha_sos = resultado_sos.get('sos_fecha')
                
                # Si se detectó el SOS, lo graficamos (Línea Verde)
                if fecha_sos is not None:
                    fecha_sos = pd.Timestamp(fecha_sos)
                    
                    fig.add_vline(x=fecha_sos, line_width=1.8, line_dash="dash", line_color="green")
                    
                    if not label_sos_agregado:
                        fig.add_trace(go.Scatter(x=[fecha_sos], y=[None], mode='lines', line=dict(color='green', dash='dash'), name='Start of Season (SOS)', showlegend=True))
                        label_sos_agregado = True
                    
                    fig.add_annotation(
                        x=fecha_sos, y=0.95, yref="paper",
                        text=f"SOS: {fecha_sos.strftime('%Y-%m-%d')}",
                        showarrow=False, textangle=-90, xanchor="left", yanchor="top",
                        font=dict(color="green", size=9, family="Arial", weight="bold"),
                        bgcolor="rgba(255, 255, 255, 0.75)"
                    )

        # F. Procesar segmento final abierto (último valle → fin de la serie)
        # segmentar_ciclos solo retorna segmentos entre valles consecutivos;
        # el segmento abierto después del último valle no se incluye.
        ultimo_valle = segmentos[-1][1]
        if ultimo_valle < df_suave.index[-1]:
            df_final = df_suave.loc[df_suave.index >= ultimo_valle, parcela]
            if not df_final.empty:
                resultado_sos = detectar_sos(
                    serie=df_final.values,
                    fechas=df_final.index,
                    factor=factor_sos,
                    metodo="seasonal_amplitude",
                    ventana_busqueda=(ultimo_valle, df_suave.index.max())
                )
                fecha_sos = resultado_sos.get('sos_fecha')
                if fecha_sos is not None:
                    fecha_sos = pd.Timestamp(fecha_sos)
                    fig.add_vline(x=fecha_sos, line_width=1.8, line_dash="dash", line_color="green")
                    if not label_sos_agregado:
                        fig.add_trace(go.Scatter(
                            x=[fecha_sos], y=[None], mode='lines',
                            line=dict(color='green', dash='dash'),
                            name='Start of Season (SOS)', showlegend=True
                        ))
                        label_sos_agregado = True
                    fig.add_annotation(
                        x=fecha_sos, y=0.95, yref="paper",
                        text=f"SOS: {fecha_sos.strftime('%Y-%m-%d')}",
                        showarrow=False, textangle=-90, xanchor="left", yanchor="top",
                        font=dict(color="green", size=9, family="Arial", weight="bold"),
                        bgcolor="rgba(255, 255, 255, 0.75)"
                    )

        # E. Diseño y Estética del Layout
        fig.update_layout(
            title=dict(
                text=f"Dinámica Fenológica Maíz: {indice_nombre} en {parcela} (Whittaker + SOS)",
                font=dict(size=14, weight='bold')
            ),
            xaxis_title="Fecha del Calendario Agrícola",
            yaxis_title=f"Valor del Índice ({indice_nombre})",
            xaxis=dict(range=[df_suave.index.min(), df_suave.index.max()]),
            template="plotly_white",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            width=1200,
            height=600,
            margin=dict(t=90, b=40, l=50, r=40)
        )

        fig.show()

    # 3. Lanzar interact con el dropdown de parcelas
    interact(
        actualizar_grafico, 
        parcela=widgets.Dropdown(
            options=parcelas, 
            value=parcelas[0], 
            description="🌱 Parcela:"
        )
    )


def visualizar_parcelas_en_mapa():
    """Lee las parcelas vigentes de la BD activa (real o pruebas) y las

    despliega en un mapa interactivo de Folium con capas satelital y de calles.
    """
    import folium
    import geopandas as gpd
    from utils.conexionDB import get_db_path
    from config import LAYERS_GPKG

    try:
        gdf = gpd.read_file(str(get_db_path()), layer=LAYERS_GPKG["parcelas"])

        
        gdf = gdf.to_crs(epsg=4326)

        centro = gdf.geometry.centroid
        lat_centro = centro.y.mean()
        lon_centro = centro.x.mean()

        m = folium.Map(location=[lat_centro, lon_centro], zoom_start=13, width="800px", height="600px")

        folium.TileLayer(
            tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
            attr="Google Satellite",
            name="Google Satélite",
            overlay=False,
            control=True,
        ).add_to(m)

        folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)

        folium.GeoJson(
            gdf,
            name="Parcelas Vigentes",
            style_function=lambda feature: {
                "fillColor": "#22c55e",
                "color": "#15803d",
                "weight": 2,
                "fillOpacity": 0.4,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=[col for col in gdf.columns if col != "geometry"][:5],
                aliases=[f"{col}:" for col in gdf.columns if col != "geometry"][:5],
                localize=True,
            ),
        ).add_to(m)

        folium.LayerControl().add_to(m)

        return m

    except Exception as e:
        print(f"Error al cargar parcelas desde la BD activa: {e}")

def visualizar_parcelas_hvplot():
    """Lee las parcelas vigentes de la BD activa (real o pruebas) y las despliega en un mapa interactivo con hvplot."""
    import geopandas as gpd
    from utils.conexionDB import get_db_path
    from config import LAYERS_GPKG
    import hvplot.pandas  # activa hvplot para GeoDataFrames

    gdf = gpd.read_file(str(get_db_path()), layer=LAYERS_GPKG["parcelas"]).to_crs(epsg=4326)

    # Mapa interactivo con tamaño ampliado
    return gdf.hvplot(
        geo=True, 
        tiles="OSM", 
        hover_cols=["id_parcela"],
        tools=["hover"],  # <-- Forzamos la herramienta de hover de Bokeh
        width=900,       
        height=600,      
        title="Visualización de Parcelas"
    )
    # return gdf.explore(column="id_parcela", tooltip=["id_parcela"], width=900, height=600)

def visualizar_geojson(ruta_geojson):
    """Lee un archivo GeoJSON y lo despliega en un mapa interactivo

    con capas base satelital y de calles.
    """
    try:
        # 1. Leer el archivo GeoJSON
        gdf = gpd.read_file(ruta_geojson)

        # 2. Asegurar que esté en WGS84 (EPSG:4326) para que Folium pueda leerlo bien
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs(epsg=4326)

        # 3. Calcular el centro geométrico de tus polígonos para enfocar el mapa automáticamente
        centro = gdf.geometry.centroid
        lat_centro = centro.y.mean()
        lon_centro = centro.x.mean()

        # 4. Crear el mapa base interactivo apuntando al centro de tus datos
        m = folium.Map(location=[lat_centro, lon_centro], zoom_start=13)

        # 5. Agregar capa satelital (esencial para ver tus parcelas de maíz)
        folium.TileLayer(
            tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
            attr="Google Satellite",
            name="Google Satélite",
            overlay=False,
            control=True,
        ).add_to(m)

        # Agregar capa de calles estándar por si quieres alternar
        folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)

        # 6. Agregar los polígonos del GeoJSON al mapa con estilo personalizado
        folium.GeoJson(
            gdf,
            name="Tus Polígonos",
            style_function=lambda feature: {
                "fillColor": "#22c55e",  # Verde para simular cultivo
                "color": "#15803d",  # Borde verde oscuro
                "weight": 2,
                "fillOpacity": 0.4,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=[
                    col
                    for col in gdf.columns
                    if col != "geometry" and col != "id"
                ][:5],
                aliases=[
                    f"{col}:"
                    for col in gdf.columns
                    if col != "geometry" and col != "id"
                ][:5],
                localize=True,
            ),
        ).add_to(m)

        # Control de capas para encender/apagar el satélite o tus polígonos
        folium.LayerControl().add_to(m)

        # 7. Desplegar el mapa en el Notebook
        return m

    except Exception as e:
        print(f"Error al cargar o visualizar el GeoJSON: {e}")