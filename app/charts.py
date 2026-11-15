"""Shared Plotly styling. Palette: validated reference categorical order (dataviz skill),
assigned by role, never cycled. One y-axis per chart."""

from __future__ import annotations

import plotly.graph_objects as go

# Fixed role -> categorical slot (light-mode steps; Streamlit's theme supplies surfaces).
ROLE = {
    "market": "#2a78d6",  # slot 1 blue  - SPC / market-implied
    "eof": "#eb6834",  # slot 2 orange - EOF
    "eee": "#1baf7a",  # slot 3 aqua - EEE
    "kalshi": "#eda100",  # slot 4 yellow
    "polymarket": "#6250d6",  # slot 7 violet
    "headline": "#2a78d6",
    "core": "#eb6834",
}
INK = "#52514e"  # neutral series (TPM) / secondary text
MUTED = "#8b8a85"
BAND = "rgba(139,138,133,0.14)"
DIVERGING = {"pos": "#e34948", "neg": "#2a78d6", "mid": "#c3c2b7"}


def base_layout(fig: go.Figure, title: str | None = None, y_title: str = "%", height: int = 380):
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15)) if title else None,
        height=height,
        margin=dict(l=8, r=8, t=40 if title else 10, b=8),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
        yaxis=dict(title=y_title, gridcolor="rgba(139,138,133,0.18)", zeroline=False),
        xaxis=dict(showgrid=False),
    )
    return fig


def line(fig: go.Figure, x, y, name: str, color: str, shape: str = "linear", dash=None, **kw):
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            name=name,
            mode="lines",
            line=dict(color=color, width=2, shape=shape, dash=dash),
            **kw,
        )
    )


def markers(fig: go.Figure, x, y, name: str, color: str, symbol: str = "circle", **kw):
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            name=name,
            mode="markers",
            marker=dict(color=color, size=10, symbol=symbol, line=dict(width=2, color="white")),
            **kw,
        )
    )
