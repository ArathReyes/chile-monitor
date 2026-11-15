"""Curves & Rates · Chile vs World. UST comparison pending (FRED skipped for now)."""

from __future__ import annotations

import cr_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import CAT, base_layout, line
from cr_ui import curve_caption, header, pending, text_table

from monitor.models import curves as C

TENORS = (2.0, 5.0, 10.0)


def level_table() -> pd.DataFrame:
    rows = []
    for curve in ("nom_bond", "nom_swap"):
        p = cr_data.panel(curve)
        for t in TENORS:
            if t not in p.columns or p[t].dropna().empty:
                continue
            s = p[t].dropna()
            s1, s3 = C.last_stats(s, "1Y"), C.last_stats(s, "3Y")
            rows.append(
                {
                    "Chile": f"{C.CURVES[curve].label} {C.tenor_label(t)}",
                    "level (%)": s1["value"],
                    "as of": f"{s.index[-1]:%d-%b-%y}",
                    "z 1Y": s1["z"],
                    "z 3Y": s3["z"],
                    "UST": "pending",
                    "spread vs UST": "pending",
                }
            )
    out = pd.DataFrame(rows)
    for c in ("level (%)", "z 1Y", "z 3Y"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")  # None -> NaN (blank)
    return out


def render() -> None:
    header(
        "Chile nominal rates vs UST. UST yields (FRED DGS2/5/10) are **pending**: FRED is "
        "skipped for now, so only the Chile side is shown.",
        "cr_world",
    )
    if cr_data.panel("nom_swap").empty:
        pending("Curve data not loaded. Press Refresh.")
        return
    pending(
        "Pending: UST 2Y/5Y/10Y levels, Chile − UST spreads and US forwards (FRED not enabled)."
    )
    t = level_table()
    st.dataframe(
        text_table(t, {"level (%)": "{:.2f}", "z 1Y": "{:+.2f}", "z 3Y": "{:+.2f}"}),
        width="stretch",
        hide_index=True,
    )
    st.markdown("**Chile forwards (NSS, swaps)**")
    fig = go.Figure()
    i = 0
    for curve in ("nom_swap", "real_swap"):
        for name, (a, b) in (("1y1y", (1.0, 2.0)), ("5y5y", (5.0, 10.0))):
            s = cr_data.nss_forward_history(curve, a, b)
            s = s[s.index >= (s.index.max() - pd.DateOffset(years=3))] if len(s) else s
            if len(s):
                line(fig, s.index, s.values, f"{C.CURVES[curve].label} {name}", CAT[i])
            i += 1
    st.plotly_chart(base_layout(fig, None, "%", 360), width="stretch")
    st.caption(
        "US 1y1y / 5y5y: pending (FRED). Mexico (Banxico SIE), Colombia (BanRep) and Brazil "
        "(BCB SGS) will be added with the Global Macro tab."
    )
    curve_caption(["nom_bond", "nom_swap"], "Sources: BCCh BDE; FRED pending.")
