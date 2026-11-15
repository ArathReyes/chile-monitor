"""Chile Macro · Labor & External."""

from __future__ import annotations

from datetime import date

import cm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from cm_ui import header, series_caption
from plotly.subplots import make_subplots

from monitor.models.transforms import rolling_sum


def _since(s: pd.Series, start) -> pd.Series:
    return s[s.index >= pd.Timestamp(start)]


def labor(start) -> None:
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        for sid, name, color, dash in [
            ("cl_unemp", "Unemployment (NSA)", ROLE["market"], None),
            ("cl_unemp_sa", "Unemployment (SA)", ROLE["market"], "dot"),
        ]:
            s = _since(cm_data.series(sid), start)
            line(fig, s.index, s.values, name, color, dash=dash)
        st.plotly_chart(
            base_layout(fig, "Unemployment rate (moving quarter)", "%", 300), width="stretch"
        )
        e = _since(cm_data.yoy_of("cl_employment"), start)
        fig = go.Figure(
            go.Bar(x=e.index, y=e.values, marker_color=ROLE["eee"], name="Employment y/y")
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(base_layout(fig, "Employment y/y", "%", 240), width="stretch")
    with c2:
        fig = go.Figure()
        nom = _since(cm_data.yoy_of("cl_wages_nominal"), start)
        real = _since(cm_data.yoy_of("cl_wages_real"), start)
        icmo = cm_data.yoy_of("cl_icmo")
        cpi = cm_data.series("cl_cpi_yoy")
        icmo_real = (((1 + icmo / 100) / (1 + cpi.reindex(icmo.index) / 100)) - 1) * 100
        line(fig, nom.index, nom.values, "Wages (IR) nominal", ROLE["market"])
        line(fig, real.index, real.values, "Wages (IR) real", ROLE["eof"])
        s = _since(icmo, start)
        line(fig, s.index, s.values, "Labor cost (ICMO) nominal", ROLE["eee"], dash="dot")
        s = _since(icmo_real.dropna(), start)
        line(fig, s.index, s.values, "ICMO real (computed)", ROLE["kalshi"], dash="dot")
        st.plotly_chart(base_layout(fig, "Wage growth, y/y", "%", 360), width="stretch")
        st.caption(
            "ICMO real is computed: nominal ICMO y/y deflated by CPI y/y "
            "(the BDE has no real ICMO)."
        )


def external(start) -> None:
    ex = cm_data.series("cl_exports")
    cu = cm_data.series("cl_exports_copper")
    im = cm_data.series("cl_imports")
    bal = cm_data.series("cl_trade_balance")
    noncu = (ex - cu.reindex(ex.index)).dropna()
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        for s, name, color in [
            (rolling_sum(cu, 12), "Copper exports", ROLE["eof"]),
            (rolling_sum(noncu, 12), "Non-copper exports", ROLE["market"]),
            (rolling_sum(im, 12), "Imports (FOB)", ROLE["eee"]),
        ]:
            s = _since(s.dropna() / 1000, start)
            line(fig, s.index, s.values, name, color)
        st.plotly_chart(
            base_layout(fig, "Trade, 12-month rolling sum", "USD bn", 320), width="stretch"
        )
    with c2:
        b = _since(rolling_sum(bal, 12).dropna() / 1000, start)
        ca = _since(cm_data.series("cl_ca_gdp"), start)
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=b.index,
                y=b.values,
                name="Trade balance 12m (USD bn)",
                marker_color=ROLE["market"],
            )
        )
        fig.update_layout(showlegend=True)
        st.plotly_chart(
            base_layout(fig, "Trade balance, 12-month rolling", "USD bn", 320), width="stretch"
        )
        fig = go.Figure(
            go.Bar(x=ca.index, y=ca.values, marker_color=ROLE["eof"], name="Current account % GDP")
        )
        fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
        fig.update_layout(showlegend=False)
        st.plotly_chart(
            base_layout(fig, "Current account, % of GDP (rolling 4 quarters)", "% GDP", 240),
            width="stretch",
        )


def fx_copper() -> None:
    start = pd.Timestamp(date.today().year - 3, 1, 1)
    fx = _since(cm_data.series("cl_usdclp"), start)
    cu = _since(cm_data.series("cu_lme"), start)
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=("USD/CLP (dólar observado)", "Copper, LME (USD/lb)"),
    )
    fig.add_trace(
        go.Scatter(
            x=fx.index, y=fx.values, name="USD/CLP", line=dict(color=ROLE["market"], width=2)
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=cu.index, y=cu.values, name="Copper", line=dict(color=ROLE["eof"], width=2)),
        row=2,
        col=1,
    )
    fig.update_layout(
        height=460, margin=dict(l=8, r=8, t=40, b=8), hovermode="x unified", showlegend=False
    )
    fig.update_yaxes(title_text="CLP", row=1, col=1)
    fig.update_yaxes(title_text="USD/lb", row=2, col=1)
    st.plotly_chart(fig, width="stretch")


def render() -> None:
    header(
        "Labor market and external accounts. Sources: BCCh BDE (INE labor, BCCh external accounts, "
        "dólar observado, LME copper).",
        "cm_labor",
    )
    start = date(date.today().year - 6, 1, 1)
    labor(start)
    series_caption(["cl_unemp", "cl_employment", "cl_wages_nominal", "cl_icmo"])
    external(date(date.today().year - 10, 1, 1))
    series_caption(["cl_exports", "cl_imports", "cl_trade_balance", "cl_ca_gdp"])
    st.markdown("#### USD/CLP and copper (context; full FX & Flows tab later)")
    fx_copper()
    series_caption(["cl_usdclp", "cu_lme"])
