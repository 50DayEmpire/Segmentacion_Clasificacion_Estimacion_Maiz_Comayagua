import plotly.graph_objects as go
import ipywidgets as widgets
from ipywidgets import interact
import matplotlib.pyplot as plt
import pandas as pd
from pipeline.ingesta import cargar_indices_desde_bd
from pipeline.modulo_vpm import preprocesar_indices_vpm
from pipeline.modulo_fenologico import segmentar_ciclos, detectar_sos


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
            # Identificar si estamos procesando el último segmento de la lista
            es_ultimo_segmento = (inicio == segmentos[-1][0] and fin == segmentos[-1][1])

            if es_ultimo_segmento:
                # Si es el último, extendemos la máscara hasta el fin absoluto de la serie 
                # para que detectar_sos tenga todo el contexto de la curva actual
                mask_segmento = (df_suave.index >= inicio)
            else:
                # Para los segmentos intermedios pasados, usamos los límites normales entre valles
                mask_segmento = (df_suave.index >= inicio) & (df_suave.index <= fin)
            
            df_segmento = df_suave.loc[mask_segmento, parcela]
            
            if not df_segmento.empty:
                # Ejecutar la detección pasando los límites correctos
                resultado_sos = detectar_sos(
                    serie=df_segmento.values,
                    fechas=df_segmento.index,
                    factor=factor_sos,
                    metodo="seasonal_amplitude",
                    # Si es el último ciclo, abrimos la ventana de búsqueda hasta el último dato real disponible
                    ventana_busqueda=(inicio, df_suave.index.max()) if es_ultimo_segmento else (inicio, fin)
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