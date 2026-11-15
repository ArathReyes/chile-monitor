"""Plotly builders for the Curves & Rates tab. Pure: inputs are DataFrames, output a Figure
(tested in tests/test_cr_charts.py). The x axis is numeric tenor in years, linear or square-root."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from charts import INK, base_layout

from monitor.models import curves as C

ORANGE = "#F28C28"
BOX_FILL = "rgba(31,119,180,0.18)"
BOX_LINE = "rgba(31,119,180,0.45)"
MODEL_INK = "#8b8a85"
# horizon -> (dash, opacity, width); "today" is the reference curve (and 1D in Deltas mode)
STYLE = {
    "today": ("solid", 1.0, 3),
    "1D": ("dash", 0.85, 2),
    "1W": ("dot", 0.70, 2),
    "1M": ("dashdot", 0.55, 2),
    "3M": ("longdash", 0.40, 2),
    "1Y": ("longdashdot", 0.25, 2),
}
AXIS_TICKS = (0.25, 0.5, 1, 2, 3, 5, 10, 20, 30)


def x_of(t: float | Sequence[float] | np.ndarray, scale: str):
    """Tenor (years) -> x position: identity (linear) or square root."""
    if scale == "sqrt":
        return np.sqrt(np.asarray(t, dtype=float)) if not np.isscalar(t) else math.sqrt(t)
    return np.asarray(t, dtype=float) if not np.isscalar(t) else float(t)


def tenor_axis(tenors: Sequence[float], scale: str) -> dict:
    """Ticks at the true tenor positions on the active scale, labelled 3M … 30Y."""
    ticks = [t for t in AXIS_TICKS if min(tenors) - 1e-9 <= t <= max(tenors) + 1e-9] or list(tenors)
    return dict(
        tickmode="array",
        tickvals=[float(x_of(t, scale)) for t in ticks],
        ticktext=[C.tenor_label(t) for t in ticks],
        title="tenor" + (" (√ scale)" if scale == "sqrt" else ""),
        showgrid=False,
    )


def box_widths(xs: Sequence[float]) -> list[float]:
    """Width per box = 60% of the distance to the nearest neighbour on the active scale."""
    xs = list(xs)
    if len(xs) == 1:
        return [0.5]
    out = []
    for i, x in enumerate(xs):
        gaps = [abs(x - xs[j]) for j in (i - 1, i + 1) if 0 <= j < len(xs)]
        out.append(0.6 * min(gaps))
    return out


def _fmt(v: float, unit: str) -> str:
    return f"{v:.2f}%" if unit == "pct" else f"{v:.0f}bp"


def curve_trace(
    cur: pd.DataFrame, tenors: Sequence[float], scale: str, name: str, style: str, unit: str
) -> go.Scatter:
    """One curve; gaps (unobserved tenors) break the line; stale points get hollow markers."""
    by = cur.set_index("tenor") if not cur.empty else pd.DataFrame()
    ys, sym, text = [], [], []
    for t in tenors:
        if t in by.index:
            r = by.loc[t]
            ys.append(float(r["value"]))
            sym.append("circle-open" if bool(r["stale"]) else "circle")
            note = f" · stale ({int(r['age_bd'])} bd)" if bool(r["stale"]) else ""
            text.append(
                f"{C.tenor_label(t)}: {_fmt(r['value'], unit)} on {r['obs_date']:%d-%b-%y}{note}"
            )
        else:
            ys.append(None)
            sym.append("circle")
            text.append(f"{C.tenor_label(t)}: no observation")
    dash, opacity, width = STYLE[style]
    return go.Scatter(
        x=list(x_of(list(tenors), scale)),
        y=ys,
        name=name,
        mode="lines+markers",
        connectgaps=False,
        opacity=opacity,
        line=dict(color=ORANGE, width=width, dash=dash),
        marker=dict(color=ORANGE, size=8, symbol=sym, line=dict(width=2, color=ORANGE)),
        text=text,
        hovertemplate="%{text}<extra>" + name + "</extra>",
    )


def box_traces(stats: pd.DataFrame, scale: str, window: str, unit: str) -> list[go.Box]:
    """Precomputed boxes: box p25-p75, median line, whiskers min-max."""
    xs = list(x_of(list(stats["tenor"]), scale)) if len(stats) else []
    out = []
    for (_, r), x, w in zip(stats.iterrows(), xs, box_widths(xs) if xs else [], strict=True):
        today = (
            ""
            if r["today"] is None or pd.isna(r["today"])
            else (f"<br>today {_fmt(r['today'], unit)} ({r['pct']:.0f}th pct)")
        )
        hover = (
            f"<b>{C.tenor_label(r['tenor'])}</b> · {window} window (n={int(r['n'])})<br>"
            f"min {_fmt(r['min'], unit)} · p25 {_fmt(r['p25'], unit)} · "
            f"median {_fmt(r['median'], unit)}<br>p75 {_fmt(r['p75'], unit)} · "
            f"max {_fmt(r['max'], unit)}{today}"
        )
        out.append(
            go.Box(
                x=[x],
                q1=[r["p25"]],
                median=[r["median"]],
                q3=[r["p75"]],
                lowerfence=[r["min"]],
                upperfence=[r["max"]],
                width=w,
                fillcolor=BOX_FILL,
                line=dict(color=BOX_LINE, width=1),
                name=f"{window} range",
                legendgroup="box",
                showlegend=len(out) == 0,
                hovertemplate=hover + "<extra></extra>",
            )
        )
    return out


def _delta_text(by: pd.DataFrame, t: float, unit: str) -> str:
    if t not in by.index:
        return f"{C.tenor_label(t)}: not observed on both dates"
    r = by.loc[t]
    return (
        f"{C.tenor_label(t)}: {_fmt(r['now'], unit)} ({r['now_date']:%d-%b}) vs "
        f"{_fmt(r['then'], unit)} ({r['then_date']:%d-%b}): Δ {r['delta_bp']:+.1f}bp"
    )


def snapshot_figure(
    panel: pd.DataFrame,
    unit: str,
    mode: str,
    horizons: Sequence[str],
    scale: str = "sqrt",
    window: str | None = "6M",
    ref: date | None = None,
    fit_curve: pd.DataFrame | None = None,
    model_curve: pd.DataFrame | None = None,
    title: str | None = None,
    height: int = 440,
    showlegend: bool = True,
) -> go.Figure:
    """Curve Snapshot. mode "Values": boxes (window) + today + historical curves.
    mode "Deltas": Δ vs each horizon in bp (no boxes). fit_curve / model_curve: [tenor, value]."""
    fig = go.Figure()
    ref = ref or C.latest_date(panel)
    tenors = [float(t) for t in panel.columns]
    y_unit = "%" if unit == "pct" else "bp"
    if ref is None or not tenors:
        return base_layout(fig, title, y_unit, height)
    if mode == "Values":
        today = C.curve_asof(panel, ref)
        if window:
            for b in box_traces(C.box_stats(panel, ref, window, today), scale, window, unit):
                fig.add_trace(b)
        fig.add_trace(curve_trace(today, tenors, scale, f"Today ({ref:%d-%b})", "today", unit))
        for h in horizons:
            cur = C.curve_asof(panel, C.horizon_target(ref, h))
            used = max(cur["obs_date"]) if len(cur) else C.horizon_target(ref, h)
            fig.add_trace(curve_trace(cur, tenors, scale, f"{h} ({used:%d-%b})", h, unit))
        for extra, label, dash in ((fit_curve, "NSS fit", "dot"), (model_curve, "Model", "dash")):
            if extra is not None and len(extra):
                fig.add_trace(
                    go.Scatter(
                        x=list(x_of(list(extra["tenor"]), scale)),
                        y=list(extra["value"]),
                        name=label,
                        mode="lines",
                        line=dict(color=MODEL_INK, width=1.5, dash=dash),
                        hovertemplate=f"{label} %{{y:.2f}}<extra></extra>",
                    )
                )
    else:
        factor = 100.0 if unit == "pct" else 1.0
        for h in horizons:
            d = C.curve_deltas(panel, ref, h, factor)
            by = d.set_index("tenor")
            ys = [float(by.loc[t, "delta_bp"]) if t in by.index else None for t in tenors]
            text = [_delta_text(by, t, unit) for t in tenors]
            style = "today" if h == "1D" else h
            dash, opacity, width = STYLE[style]
            fig.add_trace(
                go.Scatter(
                    x=list(x_of(tenors, scale)),
                    y=ys,
                    name=f"Δ {h}",
                    mode="lines+markers",
                    connectgaps=False,
                    opacity=opacity,
                    line=dict(color=ORANGE, width=width, dash=dash),
                    marker=dict(color=ORANGE, size=8),
                    text=text,
                    hovertemplate="%{text}<extra></extra>",
                )
            )
        fig.add_hline(y=0, line=dict(color=INK, width=1.5))
        y_unit = "Δ bp"
    base_layout(fig, title, y_unit, height)
    fig.update_layout(
        hovermode="closest",
        xaxis=tenor_axis(tenors, scale),
        showlegend=showlegend,
        boxmode="overlay",
    )
    return fig


def fit_line(params: dict | None, lo: float, hi: float, n: int = 80) -> pd.DataFrame | None:
    if not params:
        return None
    t = np.linspace(lo, hi, n)
    return pd.DataFrame({"tenor": t, "value": C.nss_yield(t, params)})
