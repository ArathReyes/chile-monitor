"""Chile Macro · Inflation."""

from __future__ import annotations

from datetime import date

import cm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from cm_ui import band, header, pending, series_caption

from monitor.models.transforms import contributions_yoy

SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
OTHER = "#8b8a85"


def _since(s: pd.Series, start) -> pd.Series:
    return s[s.index >= pd.Timestamp(start)]


def headline_core(start) -> go.Figure:
    fig = go.Figure()
    band(fig)
    for sid, name, color, kind in [
        ("cl_cpi_yoy", "CPI y/y (BCCh splice)", ROLE["headline"], "level"),
        ("cl_cpi_core_spliced", "Core SAE y/y (INE splice)", ROLE["core"], "yoy"),
        ("cl_cpi_sv", "Sin volátiles y/y (BCCh)", ROLE["eee"], "yoy"),
    ]:
        s = cm_data.series(sid) if kind == "level" else cm_data.yoy_of(sid)
        s = _since(s, start)
        line(fig, s.index, s.values, name, color)
    return base_layout(fig, "Headline vs core inflation", "% y/y", 380)


def mm_bars(start) -> go.Figure:
    h = _since(cm_data.series("cl_cpi_mm_hist"), start)
    c = _since(cm_data.series("cl_cpi_core_mm"), start)
    fig = go.Figure()
    fig.add_trace(
        go.Bar(x=h.index, y=h.values, name="CPI m/m (INE official)", marker_color=ROLE["headline"])
    )
    line(fig, c.index, c.values, "Core SAE m/m", ROLE["core"])
    fig.update_layout(bargap=0.25)
    return base_layout(fig, "Monthly CPI", "% m/m", 320)


def pairs(start) -> go.Figure:
    fig = go.Figure()
    for sid, name, color, dash in [
        ("cl_cpi_goods", "Goods", ROLE["headline"], None),
        ("cl_cpi_services", "Services", ROLE["core"], None),
        ("cl_cpi_tradables", "Tradables", ROLE["eee"], "dot"),
        ("cl_cpi_nontradables", "Non-tradables", ROLE["kalshi"], "dot"),
    ]:
        s = _since(cm_data.yoy_of(sid), start)
        line(fig, s.index, s.values, name, color, dash=dash)
    band(fig)
    return base_layout(fig, "Goods vs services · tradables vs non-tradables", "% y/y", 340)


def contributions() -> None:
    w = cm_data.cpi_weights()
    if not w:
        pending("Division weights not found (config/cpi_weights.yaml).")
        return
    weights = {k: v["weight"] for k, v in w["weights"].items()}
    names = {k: v["name"] for k, v in w["weights"].items()}
    comps = cm_data.wide(tuple(weights))
    contrib = contributions_yoy(comps, weights).dropna(how="all")
    if contrib.empty:
        st.caption("Contributions need 13 months of 2023-base division data.")
        return
    top = contrib.abs().iloc[-1].sort_values(ascending=False).index[:6]
    other = contrib.drop(columns=top).sum(axis=1)
    fig = go.Figure()
    for i, k in enumerate(top):
        fig.add_trace(go.Bar(x=contrib.index, y=contrib[k], name=names[k], marker_color=SLOTS[i]))
    fig.add_trace(go.Bar(x=contrib.index, y=other, name="Other 7 divisions", marker_color=OTHER))
    total = contrib.sum(axis=1)
    fig.add_trace(
        go.Scatter(
            x=total.index,
            y=total,
            name="CPI y/y (sum)",
            mode="lines+markers",
            line=dict(color="#0b0b0b", width=2),
            marker=dict(size=6),
        )
    )
    fig.update_layout(barmode="relative", bargap=0.2)
    st.plotly_chart(
        base_layout(fig, "Contributions to y/y CPI by division (pp)", "pp", 380), width="stretch"
    )
    st.caption(
        f"Weights: {w['method']} ({w['sample']}; max residual {w['max_abs_residual_index_pts']} "
        "index pts) — see config/cpi_weights.yaml. Top 6 divisions by latest contribution; "
        "the rest grouped as 'Other'. Available from Jan 2024 (base 2023 starts Jan 2023)."
    )


def breakevens(start) -> go.Figure:
    fig = go.Figure()
    for sid, name, color, dash in [
        ("cl_bei_swap_1y", "Swap BEI 1y", ROLE["market"], None),
        ("cl_bei_swap_2y", "Swap BEI 2y", ROLE["core"], None),
        ("cl_bei_swap_3y2y", "Swap fwd 3y in 2y", ROLE["eee"], None),
        ("cl_bei_bond_2y", "Bond BEI 2y (BCP−BCU)", ROLE["kalshi"], "dot"),
    ]:
        s = _since(cm_data.series(sid), start)
        line(fig, s.index, s.values, name, color, dash=dash)
    for sid, name in [("eee_cpi_yoy_11m", "EEE 11m"), ("eee_cpi_yoy_23m", "EEE 23m")]:
        s = _since(cm_data.series(sid), start)
        fig.add_trace(
            go.Scatter(
                x=s.index,
                y=s.values,
                name=name,
                mode="markers",
                marker=dict(
                    size=7,
                    symbol="diamond" if "11" in name else "square",
                    color="#52514e",
                    line=dict(width=1, color="white"),
                ),
            )
        )
    band(fig)
    return base_layout(
        fig, "Market-implied inflation (BCCh 'compensación inflacionaria') vs EEE", "%", 360
    )


def render() -> None:
    header("Sources: BCCh BDE (INE CPI series, BCCh splices, EEE, breakevens).", "cm_inflation")
    years = st.select_slider(
        "History",
        options=[3, 5, 10, 20, 26],
        value=10,
        key="cm_infl_years",
        format_func=lambda y: f"{y}y",
    )
    start = date(date.today().year - years, 1, 1)
    st.plotly_chart(headline_core(start), width="stretch")
    series_caption(["cl_cpi_yoy", "cl_cpi_core_spliced", "cl_cpi_sv"])
    st.caption(
        "Splicing: BCCh (G073) and INE (F074 'empalmada') official spliced series, base 2023=100. "
        "No index levels are mixed across bases here."
    )
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(mm_bars(date(date.today().year - 3, 1, 1)), width="stretch")
    with c2:
        st.plotly_chart(pairs(start), width="stretch")
    contributions()
    st.plotly_chart(breakevens(date(date.today().year - 3, 1, 1)), width="stretch")
    series_caption(["cl_bei_swap_1y", "cl_bei_swap_2y", "cl_bei_bond_2y", "eee_cpi_yoy_11m"])
    st.caption(
        "Breakevens as published by the BCCh (BDE F022.SWSP/SWFW/BOSP). No market-implied "
        "monthly CPI: there are no public short-end UF swaps."
    )
