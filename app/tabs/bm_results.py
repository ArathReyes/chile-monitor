"""Bond Monitor · Auction Results (history accumulates; refresh-on-consult via get_results)."""

from __future__ import annotations

from datetime import date, timedelta

import bm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from bm_ui import header, job_caption, pending, text_table
from charts import CAT, INK, base_layout, line

TYPE_COLOR = {"Letra": CAT[0], "BTP": CAT[1], "BTU": CAT[2]}


def latest_card(r: pd.DataFrame) -> None:
    ok = r[r["status"] == "ok"]
    if ok.empty:
        return
    d = ok["auction_date"].max()
    st.markdown(f"**Latest auction: {d:%a %d-%b-%Y}** (vs each bond's previous 5 auctions)")
    cur = ok[ok["auction_date"] == d]
    cols = st.columns(max(len(cur), 1))
    for col, x in zip(cols, cur.itertuples(), strict=False):
        prev = ok[(ok["nemo"] == x.nemo) & (ok["auction_date"] < d)].head(5)
        btc_avg = prev["bid_to_cover"].mean() if len(prev) else None
        rate_prev = prev["rate"].iloc[0] if len(prev) else None
        col.metric(
            f"{x.bond} · {x.nemo}",
            f"{x.rate:.2f}%",
            None if rate_prev is None else f"{(x.rate - rate_prev) * 100:+.0f}bp vs last auction",
            delta_color="off",
        )
        btc = f"{x.bid_to_cover:.2f}x" if pd.notna(x.bid_to_cover) else "–"
        avg = f" (prev-5 avg {btc_avg:.2f}x)" if btc_avg and pd.notna(btc_avg) else ""
        col.caption(
            f"Bid-to-cover {btc}{avg} · {x.allocated:,.0f} of {x.cupo:,.0f} {x.unit}"
            + (f" · concession {x.concession_bp:+.0f}bp" if pd.notna(x.concession_bp) else "")
        )


def _note(suspect: bool, note) -> str:
    flag = "suspect: rate inconsistent with curve; " if suspect else ""
    return flag + (note if isinstance(note, str) else "")


def history_table(r: pd.DataFrame) -> pd.DataFrame:
    t = pd.DataFrame(
        {
            "Date": r["auction_date"].dt.strftime("%d-%b-%y"),
            "Bond": r["bond"],
            "Nemo": r["nemo"],
            "Type": r["instrument_type"],
            "Unit": r["unit"],
            "Cupo": r["cupo"],
            "Demand": r["demand"],
            "Allocated": r["allocated"],
            "Bid-to-cover": r["bid_to_cover"],
            "Rate %": r["rate"],
            "Concession bp": r["concession_bp"],
            "% banks": r["pct_banks"],
            "% AFP/ins/brokers": r["pct_afp_ins"],
            "Status": r["status"],
            "Source": r["source"],
            "Revised": r["revised"].map({True: "yes", False: ""}),
            "Note": [_note(s, n) for s, n in zip(r["suspect"], r["note"], strict=True)],
        }
    )
    return t


def render() -> None:
    header(
        "Every auction result Hacienda has published: the live page (latest auction only) "
        "accumulates from the first run; 2021–2026 backfilled from the yearly consolidated "
        "files. Opening this tab scrapes first if a past auction's result is missing.",
        "bm_res",
    )
    r = bm_data.results_enriched()
    if r.empty:
        pending(
            "No auction results stored. Press Refresh, or run "
            "`uv run python -m monitor.sources.hacienda backfill`."
        )
        return
    missing = bm_data.missing_dates()
    latest_card(r)
    if missing:
        st.caption(
            "No result stored yet for "
            + ", ".join(f"{d:%d-%b}" for d in missing)
            + ": the live page only shows the latest auction; the evening job reloads "
            "Hacienda's consolidated file to fill gaps."
        )

    c1, c2, c3 = st.columns([1.2, 2, 1.6])
    types = c1.multiselect(
        "Type", ["Letra", "BTP", "BTU"], default=["Letra", "BTP", "BTU"], key="bm_r_t"
    )
    nemos = c2.multiselect("Nemo", sorted(r["nemo"].unique()), key="bm_r_n")
    lo = max(r["auction_date"].min().date(), date.today() - timedelta(days=730))
    rng = c3.date_input("Date range", (lo, r["auction_date"].max().date()), key="bm_r_d")
    f = r[r["instrument_type"].isin(types)]
    if nemos:
        f = f[f["nemo"].isin(nemos)]
    if isinstance(rng, tuple) and len(rng) == 2:
        f = f[(f["auction_date"].dt.date >= rng[0]) & (f["auction_date"].dt.date <= rng[1])]
    fmt = {
        "Cupo": "{:,.0f}",
        "Demand": "{:,.0f}",
        "Allocated": "{:,.0f}",
        "Bid-to-cover": "{:.2f}",
        "Rate %": "{:.2f}",
        "Concession bp": "{:+.0f}",
        "% banks": "{:.0f}",
        "% AFP/ins/brokers": "{:.0f}",
    }
    st.dataframe(text_table(history_table(f), fmt), hide_index=True, width="stretch", height=380)
    st.caption(
        f"{len(f)} auctions shown ({(f['status'] == 'desierta').sum()} void). Units: Letras/BTP "
        "MM CLP, BTU thousand UF. Concession = clearing rate − curve yield at the bond's maturity "
        "on T−1 (swap NSS fit + observed bond−swap spread at the BDE benchmarks), **vs fitted "
        "curve T−1, a proxy, not a when-issued tail**; only for maturities inside the benchmark "
        "range (nominal 2–10Y, real 2–20Y). '-' allocations count as zero."
    )
    ok = f[f["status"] == "ok"]
    a, b = st.columns(2)
    fig = go.Figure()
    for t in ("Letra", "BTP", "BTU"):
        s = ok[ok["instrument_type"] == t]
        if len(s):
            fig.add_trace(
                go.Scatter(
                    x=s["auction_date"],
                    y=s["bid_to_cover"],
                    name=t,
                    mode="markers",
                    marker=dict(color=TYPE_COLOR[t], size=8, line=dict(width=1, color="white")),
                    text=s["nemo"],
                    hovertemplate="%{text}: %{y:.2f}x<extra>%{x|%d-%b-%y}</extra>",
                )
            )
    base_layout(fig, "Bid-to-cover", "x", 340)
    fig.update_layout(hovermode="closest")
    a.plotly_chart(fig, width="stretch")
    m = ok.assign(month=ok["auction_date"].dt.to_period("M").dt.to_timestamp())
    g = m.groupby("month").apply(
        lambda x: pd.Series(
            {
                "banks": (x["banks"].fillna(0)).sum(),
                "others": (x["afp_ins_brokers"].fillna(0)).sum(),
            }
        ),
        include_groups=False,
    )
    tot = g.sum(axis=1).replace(0, float("nan"))
    fig2 = go.Figure()
    for col, name, color in (
        ("banks", "Banks", CAT[0]),
        ("others", "AFP / insurers / brokers", CAT[1]),
    ):
        fig2.add_trace(
            go.Bar(
                x=g.index,
                y=100 * g[col] / tot,
                name=name,
                marker=dict(color=color, line=dict(width=0)),
            )
        )
    base_layout(fig2, "Allocation mix by month (% of allocated, native units)", "%", 340)
    fig2.update_layout(barmode="stack", hovermode="x unified")
    b.plotly_chart(fig2, width="stretch")

    st.markdown("**Clearing rate vs curve yield on T−1**")
    choices = ok[ok["curve_t1"].notna()]["nemo"].value_counts()
    if choices.empty:
        st.caption("No auctions inside the curve-proxy range in this selection.")
    else:
        pick = st.selectbox(
            "Bond",
            list(choices.index),
            key="bm_r_bond",
            format_func=lambda n: f"{n} ({choices[n]} auctions)",
        )
        s = ok[(ok["nemo"] == pick)].sort_values("auction_date")
        fig3 = go.Figure()
        line(fig3, s["auction_date"], s["curve_t1"], "Curve yield T−1 (proxy)", INK, dash="dot")
        fig3.add_trace(
            go.Scatter(
                x=s["auction_date"],
                y=s["rate"],
                name="Clearing rate",
                mode="markers+lines",
                line=dict(color=CAT[1], width=2),
                marker=dict(size=8),
            )
        )
        st.plotly_chart(base_layout(fig3, None, "%", 320), width="stretch")
    job_caption(
        ["hacienda.results"], "Hacienda ODP (live page + consolidated files); curves: BCCh BDE"
    )
