"""Chile Macro · Overview."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import cm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from cm_ui import band, header, series_caption

from monitor.models.macro_surprises import ACTIVITY, INFLATION, surprise_index


def _kpi(col, label: str, value: str, delta: str | None = None, help_: str | None = None) -> None:
    col.metric(label, value, delta, delta_color="off", help=help_)


def _short(dt) -> str:
    return "n/a" if dt is None else f"{dt:%d %b %H:%M}"


def _cons(ind: str) -> str:
    r = cm_data.surprise_row(ind)
    if r is None:
        return ""
    return f"EEE {r.consensus:.1f} · surprise {r.surprise:+.1f} ({r.reference_period})"


def kpis() -> None:
    c = st.columns(4)
    mm, mm_prev, mm_d = cm_data.latest(cm_data.series("cl_cpi_mm_hist"))
    y, y_prev, _ = cm_data.latest(cm_data.series("cl_cpi_yoy"))
    nxt = cm_data.fmt_release(cm_data.next_release("cpi"))
    if mm is not None:
        _kpi(
            c[0],
            f"CPI m/m ({mm_d:%b %Y})",
            f"{mm:.1f}%",
            _cons("cpi_mm"),
            f"INE official. Next release: {nxt}",
        )
    if y is not None:
        _kpi(
            c[1],
            "CPI y/y",
            f"{y:.1f}%",
            f"prev {y_prev:.1f}% · next {_short(cm_data.next_release('cpi'))}",
        )
    core, core_prev, _ = cm_data.latest(cm_data.yoy_of("cl_cpi_core_spliced"))
    if core is not None:
        _kpi(c[2], "Core (SAE) y/y", f"{core:.1f}%", f"prev {core_prev:.1f}%")
    sv, sv_prev, _ = cm_data.latest(cm_data.yoy_of("cl_cpi_sv"))
    if sv is not None:
        _kpi(c[3], "Sin volátiles y/y", f"{sv:.1f}%", f"prev {sv_prev:.1f}%")
    c = st.columns(4)
    im, im_prev, im_d = cm_data.latest(cm_data.yoy_of("cl_imacec").round(1))
    if im is not None:
        _kpi(
            c[0],
            f"IMACEC y/y ({im_d:%b %Y})",
            f"{im:.1f}%",
            _cons("imacec_yoy") or f"prev {im_prev:.1f}%",
            "Next release: BCCh calendar on hold (not loaded).",
        )
    nm, _, _ = cm_data.latest(cm_data.yoy_of("cl_imacec_nonmining").round(1))
    if nm is not None:
        _kpi(c[1], "Non-mining IMACEC y/y", f"{nm:.1f}%", _cons("imacec_nm_yoy"))
    u, u_prev, u_d = cm_data.latest(cm_data.series("cl_unemp"))
    if u is not None:
        _kpi(
            c[2],
            f"Unemployment ({u_d:%b %Y}, NSA)",
            f"{u:.1f}%",
            f"prev {u_prev:.1f}% · next {_short(cm_data.next_release('unemployment'))}",
            "Moving quarter ending in the month shown. No public consensus in the BDE.",
        )
    uf, _ = cm_data.uf_known()
    today = pd.Timestamp(date.today())
    if not uf.empty and today in uf.index:
        fx, fx_prev, fx_d = cm_data.latest(cm_data.series("cl_usdclp"))
        _kpi(
            c[3],
            "UF today · USD/CLP",
            f"{uf.loc[today]:,.2f} · {fx:,.0f}" if fx else f"{uf.loc[today]:,.2f}",
            f"USD/CLP {fx - fx_prev:+.1f} ({fx_d:%d %b})" if fx and fx_prev else None,
        )


def mini_charts() -> None:
    c1, c2 = st.columns(2)
    start = date(date.today().year - 5, 1, 1)
    with c1:
        fig = go.Figure()
        band(fig)
        y = cm_data.series("cl_cpi_yoy", start)
        core = cm_data.yoy_of("cl_cpi_core_spliced")
        core = core[core.index >= pd.Timestamp(start)]
        line(fig, y.index, y.values, "CPI y/y", ROLE["headline"])
        line(fig, core.index, core.values, "Core (SAE) y/y", ROLE["core"])
        st.plotly_chart(base_layout(fig, "Inflation vs target band", "%", 300), width="stretch")
    with c2:
        fig = go.Figure()
        im = cm_data.yoy_of("cl_imacec")
        im = im[im.index >= pd.Timestamp(start)]
        nm = cm_data.yoy_of("cl_imacec_nonmining")
        nm = nm[nm.index >= pd.Timestamp(start)]
        line(fig, im.index, im.values, "IMACEC y/y", ROLE["market"])
        line(fig, nm.index, nm.values, "Non-mining y/y", ROLE["eee"])
        fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
        st.plotly_chart(base_layout(fig, "Activity", "%", 300), width="stretch")
    series_caption(["cl_cpi_yoy", "cl_cpi_core_spliced", "cl_imacec", "cl_imacec_nonmining"])


def small_index() -> None:
    s = cm_data.surprises()
    if s.empty:
        return
    inf = surprise_index(s, INFLATION)
    act = surprise_index(s, ACTIVITY)
    since = pd.Timestamp(date.today().year - 3, 1, 1)
    fig = go.Figure()
    line(
        fig,
        inf[inf.index >= since].index,
        inf[inf.index >= since].values,
        "Inflation",
        ROLE["headline"],
    )
    line(
        fig, act[act.index >= since].index, act[act.index >= since].values, "Activity", ROLE["eee"]
    )
    fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
    st.plotly_chart(
        base_layout(fig, "Chile Macro Surprise Index (EEE consensus, half-life 3m)", "z", 260),
        width="stretch",
    )


def upcoming() -> None:
    r = cm_data.releases()
    if r.empty:
        st.caption("No release calendar loaded. Press Refresh.")
        return
    now = datetime.now()
    nxt = r[(r["release_local"] >= now) & (r["release_local"] <= now + timedelta(days=14))]
    nxt = nxt[nxt["indicator"] != "other"] if (nxt["indicator"] != "other").any() else nxt
    st.markdown("**Next 2 weeks (INE)**")
    if nxt.empty:
        st.caption("No tracked INE releases in the next 14 days.")
    else:
        st.dataframe(
            nxt[["release_local", "title"]].rename(
                columns={"release_local": "Santiago time", "title": "Release"}
            ),
            hide_index=True,
            width="stretch",
        )
    st.caption("BCCh releases (IMACEC, GDP, trade): calendar on hold.")


def render() -> None:
    header(
        "Latest prints with EEE consensus and surprise. Sources: BCCh BDE (INE data), INE agenda.",
        "cm_overview",
    )
    kpis()
    mini_charts()
    c1, c2 = st.columns([3, 2])
    with c1:
        small_index()
    with c2:
        upcoming()
