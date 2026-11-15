"""Chile Macro · Activity."""

from __future__ import annotations

from datetime import date

import cm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from cm_ui import header, series_caption

from monitor.models.transforms import mom, qoq_saar, saar


def _since(s: pd.Series, start) -> pd.Series:
    return s[s.index >= pd.Timestamp(start)]


def _zero(fig) -> None:
    fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))


def render() -> None:
    header(
        "IMACEC and GDP. Source: BCCh BDE (national accounts, reference 2018; EEE).", "cm_activity"
    )
    start = date(date.today().year - 5, 1, 1)
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        for sid, name, color in [
            ("cl_imacec", "IMACEC", ROLE["market"]),
            ("cl_imacec_mining", "Mining", ROLE["eof"]),
            ("cl_imacec_nonmining", "Non-mining", ROLE["eee"]),
        ]:
            s = _since(cm_data.yoy_of(sid), start)
            line(fig, s.index, s.values, name, color)
        _zero(fig)
        st.plotly_chart(base_layout(fig, "IMACEC y/y (original series)", "%", 340), width="stretch")
    with c2:
        sa = cm_data.series("cl_imacec_sa")
        m = _since(mom(sa), start)
        s3 = _since(saar(sa, 3), start)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=m.index, y=m.values, name="m/m SA", marker_color=ROLE["market"]))
        line(fig, s3.index, s3.values, "3m/3m SAAR", ROLE["eof"])
        _zero(fig)
        st.plotly_chart(base_layout(fig, "IMACEC seasonally adjusted", "%", 340), width="stretch")
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        for sid, name, color in [
            ("cl_imacec_pb", "Goods production", ROLE["market"]),
            ("cl_imacec_com", "Commerce", ROLE["eof"]),
            ("cl_imacec_serv", "Services", ROLE["eee"]),
        ]:
            s = _since(cm_data.yoy_of(sid), start)
            line(fig, s.index, s.values, name, color)
        _zero(fig)
        st.plotly_chart(base_layout(fig, "IMACEC by sector, y/y", "%", 320), width="stretch")
    with c2:
        g = cm_data.series("cl_gdp")
        gs = cm_data.series("cl_gdp_sa")
        y = _since(cm_data.yoy_of("cl_gdp"), date(date.today().year - 8, 1, 1))
        q = _since(qoq_saar(gs), date(date.today().year - 8, 1, 1))
        fig = go.Figure()
        fig.add_trace(go.Bar(x=y.index, y=y.values, name="y/y", marker_color=ROLE["market"]))
        line(fig, q.index, q.values, "q/q SAAR", ROLE["eof"])
        _zero(fig)
        st.plotly_chart(base_layout(fig, "GDP (chained volume)", "%", 320), width="stretch")
        del g
    s = cm_data.surprises()
    if not s.empty:
        imc = s[s["indicator"] == "imacec_yoy"].tail(36)
        fig = go.Figure()
        x = pd.to_datetime(imc["reference_period"])
        line(fig, x, imc["actual"], "Actual (revised)", ROLE["market"])
        fig.add_trace(
            go.Scatter(
                x=x,
                y=imc["consensus"],
                name="EEE median",
                mode="markers",
                marker=dict(size=8, color=ROLE["eee"], line=dict(width=1.5, color="white")),
            )
        )
        _zero(fig)
        st.plotly_chart(
            base_layout(fig, "IMACEC y/y: EEE expectation vs actual", "%", 300), width="stretch"
        )
        st.caption(
            "EEE asks for the IMACEC of the previous month. Actuals are today's revised values "
            "(historical first prints aren't available; real vintages accumulate from now on)."
        )
    gdp = []
    for y_ in (date.today().year, date.today().year + 1):
        try:
            v = cm_data.series(f"eee_gdp_{y_}")
        except KeyError:
            continue
        if len(v):
            gdp.append(
                {"Year": y_, "EEE GDP growth %": float(v.iloc[-1]), "Survey": v.index[-1].date()}
            )
    if gdp:
        st.dataframe(pd.DataFrame(gdp), hide_index=True, width="stretch")
    series_caption(["cl_imacec", "cl_imacec_sa", "cl_gdp", "cl_gdp_sa"])
