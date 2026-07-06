# components/graficas_series.py — Curvas EVI, LSWI y GPP por parcela
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_COLORES = {"EVI": "#2ecc71", "LSWI": "#3498db", "GPP": "#e67e22"}


def _figura_placeholder(titulo: str, indices: list[str]) -> go.Figure:
    n = len(indices) if indices else 1
    fig = make_subplots(
        rows=n, cols=1,
        shared_xaxes=True,
        subplot_titles=indices if indices else ["Sin índice seleccionado"],
        vertical_spacing=0.08,
    )
    for i, indice in enumerate(indices, start=1):
        color = _COLORES.get(indice, "#95a5a6")
        fig.add_trace(
            go.Scatter(x=[], y=[], mode="lines", name=indice,
                       line=dict(color=color, width=2)),
            row=i, col=1,
        )
        fig.add_annotation(
            text="Sin datos — conecta la base de datos",
            xref="paper", yref=f"y{i}" if i > 1 else "y",
            x=0.5, y=0, showarrow=False,
            font=dict(color="#4a5568", size=13),
            row=i, col=1,
        )
    fig.update_layout(
        title=titulo, height=220 * n,
        paper_bgcolor="#0f1117", plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff", family="sans-serif"),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    fig.update_xaxes(gridcolor="#2d3139", zerolinecolor="#2d3139", tickfont=dict(size=11))
    fig.update_yaxes(gridcolor="#2d3139", zerolinecolor="#2d3139", tickfont=dict(size=11))
    return fig


def _figura_series(
    titulo: str,
    datos: dict,
    indices: list[str],
) -> go.Figure:
    fig = make_subplots(
        rows=len(indices), cols=1,
        shared_xaxes=True,
        subplot_titles=indices,
        vertical_spacing=0.08,
    )
    for i, indice in enumerate(indices, start=1):
        color = _COLORES.get(indice, "#95a5a6")
        raw = datos["raw"].get(indice)
        smooth = datos["smoothed"].get(indice)

        if raw is not None and not raw.empty:
            fig.add_trace(
                go.Scatter(
                    x=raw.index, y=raw.values,
                    mode="markers",
                    name=f"{indice} crudo",
                    marker=dict(color=color, size=5, opacity=0.5),
                    showlegend=True,
                ),
                row=i, col=1,
            )
        if smooth is not None and not smooth.empty:
            fig.add_trace(
                go.Scatter(
                    x=smooth.index, y=smooth.values,
                    mode="lines",
                    name=f"{indice} suavizado",
                    line=dict(color=color, width=2.5),
                    showlegend=True,
                ),
                row=i, col=1,
            )

        if raw is None and smooth is None:
            fig.add_annotation(
                text="Sin datos para este índice",
                xref="paper", yref=f"y{i}" if i > 1 else "y",
                x=0.5, y=0, showarrow=False,
                font=dict(color="#4a5568", size=13),
                row=i, col=1,
            )

    fig.update_layout(
        title=titulo, height=260 * len(indices),
        paper_bgcolor="#0f1117", plot_bgcolor="#1a1d23",
        font=dict(color="#ffffff", family="sans-serif"),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="right", x=1),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    fig.update_xaxes(gridcolor="#2d3139", zerolinecolor="#2d3139", tickfont=dict(size=11))
    fig.update_yaxes(gridcolor="#2d3139", zerolinecolor="#2d3139", tickfont=dict(size=11))
    return fig


def render_series_temporales(
    datos_series: dict | None,
    filtros: dict,
    parcela_id: int | None,
) -> None:
    indices = filtros.get("indices", ["EVI", "LSWI", "GPP"])
    ciclo = filtros.get("ciclo", "primera")

    if parcela_id is None:
        st.info(
            "Selecciona una parcela en el selector de arriba para visualizar sus series temporales.",
            icon="ℹ️",
        )
        if indices:
            fig = _figura_placeholder(
                f"Series temporales — ciclo {ciclo.title()}", indices,
            )
            st.plotly_chart(fig, use_container_width=True)
        return

    if not indices:
        st.warning("Selecciona al menos un índice espectral en el panel lateral.", icon="⚠️")
        return

    if datos_series is None:
        st.warning(
            f"No hay datos de series temporales para la parcela **{parcela_id}** "
            f"en el ciclo **{ciclo}**.",
            icon="⚠️",
        )
        fig = _figura_placeholder(
            f"Parcela {parcela_id} — ciclo {ciclo.title()}", indices,
        )
        st.plotly_chart(fig, use_container_width=True)
        return

    fig = _figura_series(
        f"Parcela {parcela_id} — ciclo {ciclo.title()}",
        datos_series,
        [i for i in indices if i in ("EVI", "LSWI")],
    )
    st.plotly_chart(fig, use_container_width=True)
