# components/graficas_series.py — Curvas EVI, LSWI y GPP por parcela
"""
Renderiza los gráficos de series temporales espectrales.
Recibe DataFrames ya procesados (o None) y el dict de filtros.
No hace queries ni llama a st.cache_data.
"""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def _figura_placeholder(titulo: str, indices: list[str]) -> go.Figure:
    """
    Genera una figura vacía con anotación de 'sin datos'
    para mostrar la estructura del gráfico antes de conectar la BD.
    """
    n = len(indices) if indices else 1
    fig = make_subplots(
        rows=n, cols=1,
        shared_xaxes=True,
        subplot_titles=indices if indices else ["Sin índice seleccionado"],
        vertical_spacing=0.08,
    )

    _colores = {"EVI": "#2ecc71", "LSWI": "#3498db", "GPP": "#e67e22"}

    for i, indice in enumerate(indices, start=1):
        color = _colores.get(indice, "#95a5a6")
        fig.add_trace(
            go.Scatter(
                x=[],
                y=[],
                mode="lines",
                name=indice,
                line=dict(color=color, width=2),
            ),
            row=i, col=1,
        )
        fig.add_annotation(
            text="Sin datos — conecta la base de datos",
            xref="paper", yref=f"y{i}" if i > 1 else "y",
            x=0.5, y=0,
            showarrow=False,
            font=dict(color="#4a5568", size=13),
            row=i, col=1,
        )

    fig.update_layout(
        title=titulo,
        height=220 * n,
        paper_bgcolor="#0f1117",
        plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff", family="sans-serif"),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right",  x=1,
        ),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    fig.update_xaxes(
        gridcolor="#2d3139",
        zerolinecolor="#2d3139",
        tickfont=dict(size=11),
    )
    fig.update_yaxes(
        gridcolor="#2d3139",
        zerolinecolor="#2d3139",
        tickfont=dict(size=11),
    )
    return fig


def render_series_temporales(parcela_id: str | None, filtros: dict) -> None:
    """
    Renderiza las curvas de índices espectrales para una parcela.

    Parámetros
    ----------
    parcela_id : str | None
        Identificador de la parcela seleccionada.
    filtros : dict
        Diccionario con claves 'ciclo', 'indices', 'mostrar_sos', 'mostrar_pos'.
    """
    indices      = filtros.get("indices", ["EVI", "LSWI", "GPP"])
    mostrar_sos  = filtros.get("mostrar_sos", True)
    mostrar_pos  = filtros.get("mostrar_pos", True)
    ciclo        = filtros.get("ciclo", "primera")

    sin_parcela = (
        not parcela_id
        or parcela_id == "— Selecciona una parcela —"
    )

    if sin_parcela:
        st.info(
            "Selecciona una parcela en el selector de arriba para visualizar sus series temporales.",
            icon="ℹ️",
        )
        # Mostrar gráfica vacía como referencia de la estructura
        if indices:
            fig = _figura_placeholder(
                f"Series temporales — ciclo {ciclo.title()}",
                indices,
            )
            st.plotly_chart(fig, use_container_width=True)
        return

    if not indices:
        st.warning(
            "Selecciona al menos un índice espectral en el panel lateral.",
            icon="⚠️",
        )
        return

    # ── Gráfica placeholder hasta conectar la BD ───────────────────────────────
    st.warning(
        f"No hay datos de series temporales para la parcela **{parcela_id}** "
        f"en el ciclo **{ciclo}**. Conecta la base de datos para visualizar.",
        icon="⚠️",
    )

    fig = _figura_placeholder(
        f"Parcela {parcela_id} — ciclo {ciclo.title()}",
        indices,
    )
    st.plotly_chart(fig, use_container_width=True)
