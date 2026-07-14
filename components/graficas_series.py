# components/graficas_series.py — Curvas EVI, LSWI y GPP por parcela
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_COLORES = {"EVI": "#2ecc71", "LSWI": "#3498db", "GPP": "#e67e22"}
_COLOR_SOS = {"primera": "#2ecc71", "postrera": "#e67e22"}  # SOS por temporada


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
    ventana_fecha: pd.Timestamp | None = None,
    ventana_nombre: str | None = None,
    extrapolado: dict | None = None,
    validacion: dict | None = None,
    sos_fecha: pd.Timestamp | None = None,
    eos_fecha: pd.Timestamp | None = None,
    ciclos: pd.DataFrame | None = None,
    mostrar_sos: bool = True,
    mostrar_eos: bool = True,
    mostrar_t1: bool = False,
    mostrar_t2: bool = False,
    mostrar_t3: bool = False,
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

        if validacion is not None:
            val = validacion.get(indice)
            if val is not None and not val.empty:
                fig.add_trace(
                    go.Scatter(
                        x=val.index, y=val.values,
                        mode="lines",
                        name=f"{indice} observado (real)",
                        line=dict(color="#888888", width=2),
                        showlegend=True,
                    ),
                    row=i, col=1,
                )

        if extrapolado is not None:
            ext = extrapolado.get(indice)
            if ext is not None and not ext.empty:
                fig.add_trace(
                    go.Scatter(
                        x=ext.index, y=ext.values,
                        mode="lines",
                        name=f"{indice} proyectado",
                        line=dict(color=color, width=2.5, dash="dash"),
                        showlegend=True,
                    ),
                    row=i, col=1,
                )

        if ventana_fecha is not None:
            etiqueta = f"{ventana_nombre} {ventana_fecha.strftime('%d/%m/%Y')}" if ventana_nombre else ventana_fecha.strftime('%d/%m/%Y')
            fig.add_vline(
                x=ventana_fecha,
                line=dict(color="#e74c3c", width=1.5, dash="dash"),
                annotation_text=etiqueta,
                annotation_position="top right",
                annotation_font=dict(color="#e74c3c", size=10),
                row=i, col=1,
            )

        if sos_fecha is not None:
            fig.add_vline(
                x=sos_fecha,
                line=dict(color="#2ecc71", width=2, dash="dash"),
                annotation_text=f"SOS {sos_fecha.strftime('%d/%m/%Y')}",
                annotation_position="top left",
                annotation_font=dict(color="#2ecc71", size=10),
                row=i, col=1,
            )

        if eos_fecha is not None:
            fig.add_vline(
                x=eos_fecha,
                line=dict(color="#e74c3c", width=2, dash="dash"),
                annotation_text=f"EOS {eos_fecha.strftime('%d/%m/%Y')}",
                annotation_position="top right",
                annotation_font=dict(color="#e74c3c", size=10),
                row=i, col=1,
            )

        # ── Marcadores fenológicos multi-ciclo ────────────────────────────
        if ciclos is not None:
            for _, row in ciclos.iterrows():
                if mostrar_sos and pd.notna(row.get("sos")):
                    sos_ts = row["sos"]
                    temp_color = _COLOR_SOS.get(row.get("temporada"), "#2ecc71")
                    fig.add_vline(
                        x=sos_ts,
                        line=dict(color=temp_color, width=1.5, dash="dash"),
                        annotation=dict(
                            text=f"SOS {sos_ts.strftime('%d/%m/%Y')}",
                            textangle=-90,
                            font=dict(color=temp_color, size=11),
                            showarrow=False,
                            yref="paper", y=1,
                            xref="x", x=sos_ts,
                        ),
                        row=i, col=1,
                    )
                if mostrar_eos and pd.notna(row.get("eos")):
                    eos_ts = row["eos"]
                    fig.add_vline(
                        x=eos_ts,
                        line=dict(color="#e74c3c", width=1.5, dash="dot"),
                        annotation=dict(
                            text=f"EOS {eos_ts.strftime('%d/%m/%Y')}",
                            textangle=-90,
                            font=dict(color="#e74c3c", size=11),
                            showarrow=False,
                            yref="paper", y=1,
                            xref="x", x=eos_ts,
                        ),
                        row=i, col=1,
                    )
                if mostrar_t1 and pd.notna(row.get("t1")):
                    t1_ts = row["t1"]
                    if pd.notna(row.get("eos")) and t1_ts >= row["eos"]:
                        pass
                    else:
                        fig.add_vline(
                            x=t1_ts,
                            line=dict(color="#f39c12", width=1, dash="dot"),
                            annotation=dict(
                                text=f"T1 {t1_ts.strftime('%d/%m/%Y')}",
                                textangle=-90,
                                font=dict(color="#f39c12", size=10),
                                showarrow=False,
                                yref="paper", y=1,
                                xref="x", x=t1_ts,
                            ),
                            row=i, col=1,
                        )
                if mostrar_t2 and pd.notna(row.get("t2")):
                    t2_ts = row["t2"]
                    if pd.notna(row.get("eos")) and t2_ts >= row["eos"]:
                        pass
                    else:
                        fig.add_vline(
                            x=t2_ts,
                            line=dict(color="#9b59b6", width=1, dash="dot"),
                            annotation=dict(
                                text=f"T2 {t2_ts.strftime('%d/%m/%Y')}",
                                textangle=-90,
                                font=dict(color="#9b59b6", size=10),
                                showarrow=False,
                                yref="paper", y=1,
                                xref="x", x=t2_ts,
                            ),
                            row=i, col=1,
                        )
                if mostrar_t3 and pd.notna(row.get("t3")):
                    t3_ts = row["t3"]
                    if pd.notna(row.get("eos")) and t3_ts >= row["eos"]:
                        pass
                    else:
                        fig.add_vline(
                            x=t3_ts,
                            line=dict(color="#1abc9c", width=1, dash="dot"),
                            annotation=dict(
                                text=f"T3 {t3_ts.strftime('%d/%m/%Y')}",
                                textangle=-90,
                                font=dict(color="#1abc9c", size=10),
                                showarrow=False,
                                yref="paper", y=1,
                                xref="x", x=t3_ts,
                            ),
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
    ciclos: pd.DataFrame | None = None,
) -> None:
    indices = filtros.get("indices", ["EVI", "LSWI", "GPP"])
    ciclo = filtros.get("ciclo", "primera")
    mostrar_sos = filtros.get("mostrar_sos", True)
    mostrar_eos = filtros.get("mostrar_eos", True)
    mostrar_t1  = filtros.get("mostrar_t1", False)
    mostrar_t2  = filtros.get("mostrar_t2", False)
    mostrar_t3  = filtros.get("mostrar_t3", False)

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
        ciclos=ciclos,
        mostrar_sos=mostrar_sos,
        mostrar_eos=mostrar_eos,
        mostrar_t1=mostrar_t1,
        mostrar_t2=mostrar_t2,
        mostrar_t3=mostrar_t3,
    )
    st.plotly_chart(fig, use_container_width=True)
