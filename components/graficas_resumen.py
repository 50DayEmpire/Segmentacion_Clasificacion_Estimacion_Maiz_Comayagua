# components/graficas_resumen.py — Gráficas de resumen de producción
"""
Renderiza los gráficos de la vista Resumen.
Recibe filtros y datos agregados; no hace queries.
"""
import streamlit as st
import plotly.graph_objects as go
from config import COLORES_CICLO, RENDIMIENTO_REF


def _figura_distribucion(ciclo: str, valores: list[float]) -> go.Figure:
    color = COLORES_CICLO.get(ciclo, "#2ecc71")
    ref   = RENDIMIENTO_REF.get(ciclo, 45.0)

    fig = go.Figure()

    if valores:
        fig.add_trace(
            go.Histogram(
                x=valores,
                name="qq/ha por parcela",
                marker_color=color,
                opacity=0.8,
                nbinsx=20,
            )
        )
    else:
        fig.add_annotation(
            text="Sin datos",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(color="#4a5568", size=14),
        )

    fig.add_vline(
        x=ref,
        line_dash="dot",
        line_color="#e74c3c",
        annotation_text=f"Ref. SAG/CAN: {ref} qq/ha",
        annotation_position="top right",
        annotation_font_color="#e74c3c",
    )

    fig.update_layout(
        title=f"Distribución de rendimiento — {ciclo.title()}",
        xaxis_title="Rendimiento (qq/ha)",
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


def _figura_comparacion(ciclo: str, valores: list[float], ventana: str) -> go.Figure:
    color = COLORES_CICLO.get(ciclo, "#2ecc71")
    ref   = RENDIMIENTO_REF.get(ciclo, 45.0)

    fig = go.Figure()

    if valores:
        fig.add_trace(
            go.Bar(
                y=valores,
                marker_color=color,
                opacity=0.7,
                name="Parcelas",
                showlegend=False,
            )
        )
    else:
        fig.add_annotation(
            text="Sin datos",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(color="#4a5568", size=14),
        )

    fig.add_hline(
        y=ref,
        line_dash="dot",
        line_color="#e74c3c",
        annotation_text=f"Ref: {ref} qq/ha",
        annotation_position="top right",
        annotation_font_color="#e74c3c",
    )

    fig.update_layout(
        title=f"Rendimiento por parcela — {ciclo.title()} ({ventana})",
        xaxis_title="Parcela",
        yaxis_title="Rendimiento (qq/ha)",
        paper_bgcolor="#0f1117",
        plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff"),
        height=340,
        margin=dict(l=50, r=30, t=60, b=50),
        xaxis=dict(showticklabels=False),
    )
    fig.update_xaxes(gridcolor="#2d3139")
    fig.update_yaxes(gridcolor="#2d3139")
    return fig


def render_resumen_valle(filtros: dict, resumen: dict | None = None) -> None:
    ciclo   = filtros.get("ciclo", "primera")
    ventana = filtros.get("ventana", "T3")

    if resumen and resumen["distribucion_rendimiento"]:
        col_hist, col_bar = st.columns(2, gap="medium")
        with col_hist:
            fig_hist = _figura_distribucion(ciclo, resumen["distribucion_rendimiento"])
            st.plotly_chart(fig_hist, use_container_width=True)
        with col_bar:
            fig_bar = _figura_comparacion(ciclo, resumen["distribucion_rendimiento"], ventana)
            st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.warning(
            f"No hay datos de rendimiento para el ciclo **{ciclo}**.",
            icon="⚠️",
        )
        col_hist, col_bar = st.columns(2, gap="medium")
        with col_hist:
            fig_hist = _figura_distribucion(ciclo, [])
            st.plotly_chart(fig_hist, use_container_width=True)
        with col_bar:
            fig_bar = _figura_comparacion(ciclo, [], ventana)
            st.plotly_chart(fig_bar, use_container_width=True)
