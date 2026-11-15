"""Curves & Rates · Spreads & Shape: slopes, flies, spread histories, PCA."""

from __future__ import annotations

import cr_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import CAT, base_layout, line
from cr_ui import curve_caption, header, pending, text_table

from monitor.models import curves as C

HIST_WINDOWS = ["1Y", "3Y", "5Y", "10Y", "All"]
TENOR_COLORS = {2.0: CAT[0], 5.0: CAT[1], 10.0: CAT[2]}


def _window(s: pd.Series, w: str) -> pd.Series:
    if w == "All" or s.empty:
        return s
    return C.in_window(s, s.index[-1].date(), w)


def shape_measures(panel: pd.DataFrame) -> dict[str, pd.Series]:
    out = {n: C.slope(panel, a, b) for n, (a, b) in C.SLOPES.items()}
    out.update({n: C.fly(panel, a, b, c) for n, (a, b, c) in C.FLIES.items()})
    return out


def stats_table(measures: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for n, s in measures.items():
        if s.empty:
            rows.append({"measure": n, "current (bp)": None, "as of": "n/a: tenor not available"})
            continue
        s1, s3 = C.last_stats(s, "1Y"), C.last_stats(s, "3Y")
        rows.append(
            {
                "measure": n,
                "current (bp)": s1["value"],
                "as of": f"{s.index[-1]:%d-%b-%y}",
                "z 1Y": s1["z"],
                "pct 1Y": s1["pct"],
                "z 3Y": s3["z"],
                "pct 3Y": s3["pct"],
            }
        )
    out = pd.DataFrame(rows).set_index("measure")
    for c in ("current (bp)", "z 1Y", "pct 1Y", "z 3Y", "pct 3Y"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")  # None -> NaN (blank)
    return out


def _history_chart(series: dict[str, pd.Series], title: str, w: str, colors: list[str]):
    fig = go.Figure()
    for (name, s), color in zip(series.items(), colors, strict=False):
        s = _window(s, w)
        if not s.empty:
            line(fig, s.index, s.values, name, color)
    fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
    return base_layout(fig, title, "bp", 340)


def pca_panel() -> None:
    st.markdown("**PCA on daily changes** (dates where every tenor traded)")
    c1, c2 = st.columns(2)
    curve = c1.radio(
        "Curve",
        ["nom_swap", "nom_bond"],
        horizontal=True,
        key="cr_pca_curve",
        format_func=lambda k: C.CURVES[k].label,
    )
    w = c2.selectbox("Window", ["1Y", "3Y", "5Y", "All"], index=1, key="cr_pca_w")
    panel = cr_data.panel(curve)
    if panel.empty:
        pending("No data.")
        return
    tenors = [t for t in panel.columns if t >= (0.25 if curve == "nom_swap" else 2.0)]
    if curve == "nom_bond":
        tenors = [t for t in tenors if t in (2.0, 5.0, 10.0)]
    p = (
        panel
        if w == "All"
        else panel.loc[pd.Timestamp(C.window_start(panel.index[-1].date(), w)) :]
    )
    res = C.pca_changes(p, tenors)
    if res is None:
        pending("Not enough common observations for PCA in this window.")
        return
    a, b = st.columns(2)
    fig = go.Figure()
    for (name, col), color in zip(res.loadings.items(), CAT, strict=False):
        fig.add_trace(
            go.Scatter(
                x=[C.tenor_label(t) for t in res.loadings.index],
                y=col.values,
                name=f"{name} ({res.explained[name]:.0%})",
                mode="lines+markers",
                line=dict(color=color, width=2),
                marker=dict(size=8),
            )
        )
    a.plotly_chart(
        base_layout(fig, "Loadings (explained variance)", "loading", 320), width="stretch"
    )
    fig2 = go.Figure()
    for (name, col), color in zip(res.scores.items(), CAT, strict=False):
        line(fig2, col.index, col.values, name, color)
    b.plotly_chart(base_layout(fig2, "Cumulative factor scores", "bp", 320), width="stretch")
    st.caption(
        f"{res.n_obs} daily changes; tenors {', '.join(C.tenor_label(t) for t in tenors)}. "
        "Signs: level loadings positive, slope rises with tenor, curvature positive in the belly."
    )


def render() -> None:
    header(
        "Slopes, flies and spread histories (bp), from observed same-date points only.", "cr_shape"
    )
    if cr_data.panel("nom_swap").empty and cr_data.panel("nom_bond").empty:
        pending("Curve data not loaded. Press Refresh.")
        return
    c1, c2 = st.columns(2)
    curve = c1.selectbox(
        "Curve", C.OUTRIGHT, format_func=lambda k: C.CURVES[k].label, key="cr_shape_curve"
    )
    w = c2.selectbox("History window", HIST_WINDOWS, index=1, key="cr_shape_w")
    measures = shape_measures(cr_data.panel(curve))
    tbl = stats_table(measures)
    fmt = {
        "current (bp)": "{:.1f}",
        "z 1Y": "{:+.2f}",
        "pct 1Y": "{:.0f}",
        "z 3Y": "{:+.2f}",
        "pct 3Y": "{:.0f}",
    }
    st.dataframe(
        text_table(tbl, fmt),
        width="stretch",
    )
    a, b = st.columns(2)
    a.plotly_chart(
        _history_chart({k: measures[k] for k in C.SLOPES}, "Slopes", w, CAT), width="stretch"
    )
    b.plotly_chart(
        _history_chart({k: measures[k] for k in C.FLIES}, "Flies", w, CAT[3:]), width="stretch"
    )
    st.caption(
        "n/a: no nominal 20Y exists in the BDE (bond or swap), so 10s20s and 5s10s20s are "
        "only available for real curves."
    )
    curve_caption([curve])

    st.markdown("**Spread histories at 2Y, 5Y, 10Y**")
    cols = st.columns(3)
    for col, k in zip(cols, ["swap_spread", "swap_bei", "bond_bei"], strict=True):
        p = cr_data.panel(k)
        ser = {C.tenor_label(t): p[t].dropna() for t in (2.0, 5.0, 10.0) if t in p.columns}
        col.plotly_chart(
            _history_chart(ser, C.CURVES[k].label, w, [TENOR_COLORS[t] for t in (2.0, 5.0, 10.0)]),
            width="stretch",
            key=f"cr_sh_{k}",
        )
    curve_caption(
        ["swap_spread", "swap_bei", "bond_bei"], "Swap spread (Cámara − BTP) is not an ASW."
    )
    st.divider()
    pca_panel()
