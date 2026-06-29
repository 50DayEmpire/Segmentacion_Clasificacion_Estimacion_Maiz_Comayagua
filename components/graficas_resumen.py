# components/graficas_resumen.py — Gráficas de producción total del valle
"""
Renderiza los gráficos de la vista Resumen Valle.
Recibe filtros resueltos; no hace queries.
"""
import streamlit as st
import plotly.graph_objects as go
from config import COLORES_CICLO, RENDIMIENTO_REF


def _figura_distribucion_placeholder(ciclo: str) -> go.Figure:
    """Histograma de distribución de rendimiento por parcela."""
    color = COLORES_CICLO.get(ciclo, "#2ecc71")
    ref   = RENDIMIENTO_REF.get(ciclo, 45.0)

    fig = go.Figure()

    fig.add_trace(
        go.Histogram(
            x=[],
            name="qq/ha por parcela",
            marker_color=color,
            opacity=0.8,
            nbinsx=20,
        )
    )

    fig.add_vline(
        x=ref,
        line_dash="dot",
        line_color="#e74c3c",
        annotation_text=f"Ref. SAG/CAN: {ref} qq/ha",
        annotation_position="top right",
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
        title=f"Distribución de rendimiento — ciclo {ciclo.title()}",
        xaxis_title="Rendimiento estimado (qq/ha)",
        yaxis_title="Número de parcelas",
        paper_bgcolor="#0f1117",
        plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff"),
        height=340,
        margin=dict(l=50, r=30, t=60, b=50),
    )
    fig.update_xaxes(gridcolor="#2d3139")
    fig.update_yaxes(gridcolor="#2d3139")
    return fig


def _figura_mapa_calor_placeholder(ciclo: str, ventana: str) -> go.Figure:
    """Mapa de calor de rendimiento por parcela (placeholder sin geometría)."""
    color = COLORES_CICLO.get(ciclo, "#2ecc71")

    fig = go.Figure()

    fig.add_annotation(
        text="Mapa de calor disponible al conectar la base de datos",
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(color="#4a5568", size=14),
    )

    fig.update_layout(
        title=f"Rendimiento espacial — ciclo {ciclo.title()} · ventana {ventana}",
        paper_bgcolor="#0f1117",
        plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff"),
        height=340,
        margin=dict(l=50, r=30, t=60, b=50),
        xaxis=dict(showgrid=False, showticklabels=False),
        yaxis=dict(showgrid=False, showticklabels=False),
    )
    return fig


def render_resumen_valle(filtros: dict) -> None:
    """
    Renderiza el resumen de producción total del valle.

    Parámetros
    ----------
    filtros : dict
        Diccionario con claves 'ciclo' y 'ventana'.
    """
    ciclo   = filtros.get("ciclo", "primera")
    ventana = filtros.get("ventana", "T3")

    st.warning(
        f"No hay datos agregados para el ciclo **{ciclo}** / ventana **{ventana}**. "
        "Las gráficas se activarán al conectar la base de datos.",
        icon="⚠️",
    )

    col_hist, col_mapa = st.columns(2, gap="medium")

    with col_hist:
        fig_hist = _figura_distribucion_placeholder(ciclo)
        st.plotly_chart(fig_hist, use_container_width=True)

    with col_mapa:
        fig_calor = _figura_mapa_calor_placeholder(ciclo, ventana)
        st.plotly_chart(fig_calor, use_container_width=True)
