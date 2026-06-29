# components/graficas_estimacion.py — Gráficas de comparación estimado vs referencia
"""
Renderiza los gráficos de la vista Estimación.
Recibe filtros resueltos; no hace queries.
"""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from config import RENDIMIENTO_REF, VENTANAS, COLORES_CICLO


def _figura_dispersion_placeholder(ciclo: str, ventana: str) -> go.Figure:
    """Diagrama de dispersión vacío estimado vs referencia."""
    color = COLORES_CICLO.get(ciclo, "#2ecc71")
    ref   = RENDIMIENTO_REF.get(ciclo, 45.0)

    fig = go.Figure()

    # Línea de referencia perfecta (1:1)
    fig.add_trace(
        go.Scatter(
            x=[0, ref * 2],
            y=[0, ref * 2],
            mode="lines",
            name="Referencia 1:1",
            line=dict(color="#4a5568", dash="dash", width=1.5),
        )
    )

    # Línea horizontal de referencia SAG/CAN
    fig.add_hline(
        y=ref,
        line_dash="dot",
        line_color="#e74c3c",
        annotation_text=f"Ref. SAG/CAN: {ref} qq/ha",
        annotation_position="right",
        annotation_font_color="#e74c3c",
    )

    fig.add_annotation(
        text="Sin datos — conecta la base de datos",
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(color="#4a5568", size=14),
    )

    fig.update_layout(
        title=f"Estimado vs Referencia — ciclo {ciclo.title()} · ventana {ventana}",
        xaxis_title="Rendimiento Estimado (qq/ha)",
        yaxis_title="Rendimiento Referencia SAG/CAN (qq/ha)",
        paper_bgcolor="#0f1117",
        plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff"),
        height=420,
        margin=dict(l=50, r=30, t=60, b=50),
    )
    fig.update_xaxes(gridcolor="#2d3139", range=[0, ref * 2.2])
    fig.update_yaxes(gridcolor="#2d3139", range=[0, ref * 2.2])
    return fig


def _figura_barras_ventanas_placeholder(ciclo: str) -> go.Figure:
    """Barras de rendimiento medio estimado por ventana, vs referencia."""
    color = COLORES_CICLO.get(ciclo, "#2ecc71")
    ref   = RENDIMIENTO_REF.get(ciclo, 45.0)

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=VENTANAS,
            y=[0, 0, 0],
            name="Estimado (qq/ha)",
            marker_color=color,
            opacity=0.8,
        )
    )

    fig.add_hline(
        y=ref,
        line_dash="dot",
        line_color="#e74c3c",
        annotation_text=f"Ref. SAG/CAN: {ref} qq/ha",
        annotation_position="right",
        annotation_font_color="#e74c3c",
    )

    fig.add_annotation(
        text="Sin datos — conecta la base de datos",
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(color="#4a5568", size=14),
    )

    fig.update_layout(
        title=f"Rendimiento medio estimado por ventana — ciclo {ciclo.title()}",
        xaxis_title="Ventana de predicción",
        yaxis_title="qq/ha (media del valle)",
        paper_bgcolor="#0f1117",
        plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff"),
        height=340,
        margin=dict(l=50, r=30, t=60, b=50),
        barmode="group",
    )
    fig.update_xaxes(gridcolor="#2d3139")
    fig.update_yaxes(gridcolor="#2d3139", range=[0, ref * 2.2])
    return fig


def render_comparacion_estimacion(filtros: dict) -> None:
    """
    Renderiza la comparación de rendimiento estimado vs referencia SAG/CAN.

    Parámetros
    ----------
    filtros : dict
        Diccionario con claves 'ciclo' y 'ventana'.
    """
    ciclo   = filtros.get("ciclo", "primera")
    ventana = filtros.get("ventana", "T1")

    st.warning(
        f"No hay estimaciones disponibles para el ciclo **{ciclo}** / ventana **{ventana}**. "
        "Los gráficos se activarán al conectar la base de datos.",
        icon="⚠️",
    )

    col_disp, col_barras = st.columns([3, 2], gap="medium")

    with col_disp:
        fig_disp = _figura_dispersion_placeholder(ciclo, ventana)
        st.plotly_chart(fig_disp, use_container_width=True)

    with col_barras:
        fig_barras = _figura_barras_ventanas_placeholder(ciclo)
        st.plotly_chart(fig_barras, use_container_width=True)
