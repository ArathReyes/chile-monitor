"""Bond Monitor · Auction Impact (event study on the curve proxy)."""

from __future__ import annotations

import bm_data
import plotly.graph_objects as go
import streamlit as st
from bm_ui import header, job_caption, pending
from charts import CAT, base_layout

CAPTION = (
    "Based on fitted benchmark curves (BCCh BDE, T-1, transaction-based). Indicative, not "
    "bond-level."
)
SEG_COLOR = {"Nominal (CLP)": CAT[0], "Real (UF)": CAT[2]}
TERC_COLOR = {"Low": CAT[3], "Mid": CAT[6], "High": CAT[1]}


def _path_fig(df, by: str, colors: dict, title: str) -> go.Figure:
    cols = [f"d{k}" for k in bm_data.EVENT_OFFSETS]
    fig = go.Figure()
    order = [k for k in ("Nominal (CLP)", "Real (UF)", "Low", "Mid", "High") if k in set(df[by])]
    for key in order:
        g = df[df[by] == key]
        m = g[cols].mean()
        n = int(g["d5"].notna().sum())
        fig.add_trace(
            go.Scatter(
                x=bm_data.EVENT_OFFSETS,
                y=m.values,
                name=f"{key} (n={n})",
                mode="lines+markers",
                line=dict(color=colors.get(key, CAT[4]), width=2),
                marker=dict(size=7),
            )
        )
    fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
    fig.add_vline(x=0, line=dict(color="#8b8a85", width=1, dash="dot"), annotation_text="auction")
    base_layout(fig, title, "bp vs T−1", 340)
    fig.update_layout(
        hovermode="x unified", xaxis=dict(title="business days from auction", dtick=1)
    )
    return fig


def render() -> None:
    header(
        "How the curve moves around auctions: yield of the curve proxy at the auctioned bond's "
        "maturity, T−5 … T+5 business days, in bp vs T−1. A line rising into T−1 = yields rose "
        "(the bond cheapened) before the auction; T−5 → T−1 > 0 says the same.",
        "bm_imp",
    )
    e = bm_data.event_study()
    e = e[e[[f"d{k}" for k in bm_data.EVENT_OFFSETS]].notna().any(axis=1)] if len(e) else e
    if e.empty:
        pending("No auctions inside the curve-proxy range yet (needs results + NSS fits).")
        return
    seg = st.radio(
        "Segment", ["All", "Nominal (CLP)", "Real (UF)"], horizontal=True, key="bm_i_seg"
    )
    sub = e if seg == "All" else e[e["segment"] == seg]
    pre = (-sub["d-5"]).mean()
    post = sub["d5"].mean()
    c1, c2, c3 = st.columns(3)
    c1.metric("Auctions", f"{len(sub)}")
    c2.metric("T−5 → T−1 (avg)", f"{pre:+.1f}bp")
    c3.metric("T−1 → T+5 (avg)", f"{post:+.1f}bp")
    a, b = st.columns(2)
    a.plotly_chart(
        _path_fig(e, "segment", SEG_COLOR, "Average path: nominal vs real"), width="stretch"
    )
    b.plotly_chart(
        _path_fig(
            sub.dropna(subset=["btc_tercile"]),
            "btc_tercile",
            TERC_COLOR,
            f"By bid-to-cover tercile ({seg})",
        ),
        width="stretch",
    )
    c, d = st.columns(2)
    for col, x, title, xt in (
        (c, "bid_to_cover", "Concession vs bid-to-cover", "bid-to-cover (x)"),
        (d, "allocated_usd", "Concession vs auction size", "allocated (US$ mm)"),
    ):
        fig = go.Figure()
        for key, g in sub.groupby("segment"):
            xs = g[x] / (1e6 if x == "allocated_usd" else 1)
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=g["concession_bp"],
                    name=key,
                    mode="markers",
                    marker=dict(color=SEG_COLOR[key], size=7, opacity=0.7),
                    text=g["nemo"] + " " + g["auction_date"].dt.strftime("%d-%b-%y"),
                    hovertemplate="%{text}: %{y:+.0f}bp, %{x:.2f}<extra></extra>",
                )
            )
        fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
        base_layout(fig, title, "concession bp", 330)
        fig.update_layout(hovermode="closest", xaxis=dict(title=xt))
        col.plotly_chart(fig, width="stretch")
    st.caption(
        CAPTION + " Concession = clearing rate − curve yield at maturity on T−1; only "
        "maturities inside the benchmark range (nominal 2–10Y, real 2–20Y). Auction size "
        "replaces DV01 (nominal only, by design)."
    )
    job_caption(["hacienda.results"], "Hacienda ODP; BCCh BDE (curves)")
