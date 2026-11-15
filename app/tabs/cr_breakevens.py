"""Curves & Rates · Breakevens & Real Rates."""

from __future__ import annotations

import cr_charts
import cr_data
import mp_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import CAT, INK, base_layout, line
from cr_ui import curve_caption, header, pending
from mp_ui import series_caption

from monitor.models import curves as C

EEE = [
    ("eee_cpi_yoy_11m", 1.0, "11m"),
    ("eee_cpi_yoy_23m", 2.0, "23m"),
    ("eee_cpi_yoy_35m", 3.0, "35m"),
]
PUBLISHED = {
    "Swap 1y1y (BCCh)": "cl_bei_swap_1y1y",
    "Swap 5y5y (BCCh)": "cl_bei_swap_5y5y",
    "Bond 5y5y (BCCh)": "cl_bei_bond_5y5y",
}


def observed_forward_series(panel: pd.DataFrame, a: float, b: float) -> pd.Series:
    """Daily forward (%) from two observed tenors, on dates where both traded."""
    if a not in panel.columns or b not in panel.columns:
        return pd.Series(dtype=float)
    d = panel[[a, b]].dropna() / 100
    g = (1 + d[b]) ** b / (1 + d[a]) ** a
    return (g ** (1 / (b - a)) - 1) * 100


def forward_breakevens() -> dict[str, pd.Series]:
    out = {}
    for name, (a, b) in (("1y1y", (1.0, 2.0)), ("2y3y", (2.0, 5.0)), ("5y5y", (5.0, 10.0))):
        n = cr_data.nss_forward_history("nom_swap", a, b)
        r = cr_data.nss_forward_history("real_swap", a, b)
        both = pd.concat([n, r], axis=1, join="inner").dropna()
        out[f"Swap {name} (NSS)"] = (both.iloc[:, 0] - both.iloc[:, 1]) * 100
    for name, (a, b) in (("2y3y", (2.0, 5.0)), ("5y5y", (5.0, 10.0))):
        n = observed_forward_series(cr_data.panel("nom_bond"), a, b)
        r = observed_forward_series(cr_data.panel("real_bond"), a, b)
        both = pd.concat([n, r], axis=1, join="inner").dropna()
        out[f"Bond {name} (observed)"] = (both.iloc[:, 0] - both.iloc[:, 1]) * 100
    return out


def _window(s: pd.Series, w: str) -> pd.Series:
    return s if (w == "All" or s.empty) else C.in_window(s, s.index[-1].date(), w)


def survey_gap(be_bp: pd.Series, survey: pd.Series) -> pd.DataFrame:
    """Daily breakeven vs the latest survey published on or before each date (as-of join)."""
    if be_bp.empty or survey.empty:
        return pd.DataFrame(columns=["be", "survey", "gap_bp"])
    left = pd.DataFrame({"be": be_bp / 100}).rename_axis("d").reset_index()
    right = pd.DataFrame({"survey": survey}).rename_axis("d").reset_index()
    m = pd.merge_asof(left.sort_values("d"), right.sort_values("d"), on="d").dropna()
    m["gap_bp"] = (m["be"] - m["survey"]) * 100
    return m.set_index("d")


def render() -> None:
    header(
        "Breakeven = nominal − real, same tenor and date (bp). Forward breakevens from NSS swap "
        "fits; bond forwards only between observed tenors.",
        "cr_be",
    )
    if cr_data.panel("swap_bei").empty:
        pending("Curve data not loaded. Press Refresh.")
        return
    a, b = st.columns(2)
    for col, k in ((a, "swap_bei"), (b, "bond_bei")):
        if cr_data.panel(k).empty:
            col.caption(f"{C.CURVES[k].label}: no data")
            continue
        col.plotly_chart(
            cr_charts.snapshot_figure(
                cr_data.panel(k),
                "bp",
                "Values",
                ["1M", "1Y"],
                "sqrt",
                None,
                cr_data.ref_date(k),
                title=C.CURVES[k].label,
                height=360,
            ),
            width="stretch",
            key=f"cr_be_{k}",
        )
    curve_caption(["swap_bei", "bond_bei"])

    st.markdown("**Forward breakevens**")
    w = st.selectbox("Window", ["1Y", "3Y", "5Y", "All"], index=1, key="cr_be_w")
    fwd = forward_breakevens()
    pub = {k: mp_data.series(sid) * 100 for k, sid in PUBLISHED.items()}
    fig = go.Figure()
    for (name, s), color in zip(fwd.items(), CAT, strict=False):
        s = _window(s, w)
        if not s.empty:
            line(fig, s.index, s.values, name, color)
    for name, s in pub.items():
        s = _window(s, w)
        if not s.empty:
            line(fig, s.index, s.values, name, INK, dash="dot", opacity=0.6)
    fig.add_hline(
        y=300, line=dict(color="#8b8a85", width=1, dash="dash"), annotation_text="3% target"
    )
    st.plotly_chart(base_layout(fig, None, "bp", 380), width="stretch")
    rows = [
        {
            "forward": k,
            "latest (bp)": float(s.iloc[-1]) if len(s) else None,
            "as of": f"{s.index[-1]:%d-%b-%y}" if len(s) else "n/a",
        }
        for k, s in {**fwd, **pub}.items()
    ]
    st.dataframe(
        pd.DataFrame(rows).set_index("forward").style.format({"latest (bp)": "{:.0f}"}, na_rep=""),
        width="stretch",
    )
    st.caption(
        "Forwards: annually compounded, nominal forward − real forward. NSS fits treat the fitted "
        "quotes as zero rates (approximation, no bootstrapping). Dotted grey: BCCh-published "
        "compensación inflacionaria series, for cross-checking (their own conventions)."
    )
    series_caption(list(PUBLISHED.values()))

    st.markdown("**Breakeven vs EEE inflation expectations** (inflation risk premium proxy)")
    be = cr_data.panel("swap_bei")
    cols = st.columns(3)
    for col, (sid, tenor, lab) in zip(cols, EEE, strict=True):
        s = be[tenor].dropna() if tenor in be.columns else pd.Series(dtype=float)
        g = survey_gap(s, mp_data.series(sid))
        if g.empty:
            col.caption(f"{C.tenor_label(tenor)} vs EEE {lab}: no data")
            continue
        g = g[g.index >= g.index[-1] - pd.DateOffset(years=3)]
        fig = go.Figure()
        line(fig, g.index, g["gap_bp"], f"{C.tenor_label(tenor)} swap BE − EEE {lab}", CAT[0])
        fig.add_hline(y=0, line=dict(color=INK, width=1))
        col.plotly_chart(
            base_layout(fig, f"{C.tenor_label(tenor)} BE − EEE {lab}", "bp", 300),
            width="stretch",
            key=f"cr_irp_{lab}",
        )
        col.caption(
            f"Latest: BE {g['be'].iloc[-1]:.2f}% vs EEE {g['survey'].iloc[-1]:.2f}% "
            f"(target 3%): {g['gap_bp'].iloc[-1]:+.0f}bp"
        )
    st.caption(
        "Each day's breakeven vs the latest EEE published on or before that day (as-of join). "
        "EEE horizons 11m/23m/35m matched to the 1Y/2Y/3Y swap breakevens."
    )
    series_caption([s for s, _, _ in EEE])

    st.markdown("**Real yields: 5Y, 10Y, 20Y**")
    a, b = st.columns(2)
    for col, k in ((a, "real_bond"), (b, "real_swap")):
        p = cr_data.panel(k)
        fig = go.Figure()
        for t, color in zip((5.0, 10.0, 20.0), CAT, strict=False):
            if t in p.columns:
                s = _window(p[t].dropna(), w)
                line(fig, s.index, s.values, C.tenor_label(t), color)
        col.plotly_chart(
            base_layout(fig, C.CURVES[k].label, "%", 320), width="stretch", key=f"cr_ry_{k}"
        )
    curve_caption(["real_bond", "real_swap"], "Real bonds 20Y trade sparsely (~56 days a year).")
