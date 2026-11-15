"""Bond Monitor · Supply & Issuance."""

from __future__ import annotations

from datetime import date

import bm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from bm_ui import header, job_caption, pending, text_table
from charts import CAT, INK, base_layout, line

from monitor.models import bonds as B


def authorization() -> None:
    s = bm_data.soma().dropna(how="all")
    st.markdown("**Issuance vs annual authorization (USD)**")
    if s.empty:
        pending("SOMA authorization not loaded. Press Refresh.")
        return
    last = s.dropna().iloc[-1]
    y = s.index[-1].year
    frac = last["placed"] / last["authorized"]
    c1, c2, c3 = st.columns([1, 1, 2])
    c1.metric(f"Placed {y} (Hacienda)", f"US${last['placed']:,.0f}mm")
    c2.metric(
        "Authorized", f"US${last['authorized']:,.0f}mm", f"{frac:.0%} used", delta_color="off"
    )
    pace = B.straight_line_pace(y, last["authorized"], s.index[-1].date())
    c3.progress(
        min(frac, 1.0),
        text=f"{frac:.0%} of the authorization · straight-line pace "
        f"{pace / last['authorized']:.0%} by {s.index[-1]:%d-%b}",
    )
    r = bm_data.results_enriched()
    ytd = (
        pd.DataFrame()
        if r.empty
        else r[(r["auction_date"].dt.year == y) & (r["status"] == "ok")].sort_values("auction_date")
    )
    fig = go.Figure()
    if len(ytd):
        cum = ytd.groupby("auction_date")["allocated_usd"].sum().cumsum() / 1e6
        line(
            fig,
            cum.index,
            cum.values,
            "Cumulative auctions (US$mm at each day's FX)",
            CAT[0],
            shape="hv",
        )
    days = pd.date_range(f"{y}-01-01", f"{y}-12-31", freq="D")
    line(
        fig,
        days,
        [B.straight_line_pace(y, last["authorized"], d.date()) for d in days],
        "Straight-line pace",
        INK,
        dash="dot",
    )
    fig.add_hline(
        y=last["authorized"],
        line=dict(color=INK, width=1, dash="dash"),
        annotation_text="authorized",
    )
    fig.add_trace(
        go.Scatter(
            x=s.index,
            y=s["placed"],
            name="Placed per Hacienda (SOMA page)",
            mode="markers",
            marker=dict(color=CAT[1], size=9),
        )
    )
    st.plotly_chart(base_layout(fig, None, "US$ mm", 360), width="stretch")
    st.caption(
        "Hacienda's 'placed' figure (SOMA page, scraped daily from 15-Nov-2026) uses its "
        "own USD conversion and scope; the blue line sums this year's auction allocations "
        "at each auction day's dólar observado and UF, so the two can differ."
    )


def by_quarter() -> None:
    r = bm_data.results_enriched()
    if r.empty:
        return
    ok = r[(r["status"] == "ok") & r["allocated_usd"].notna()].copy()
    if ok.empty:
        return
    ok["quarter"] = ok["auction_date"].dt.to_period("Q").astype(str)
    ok["currency"] = ["UF" if t == "BTU" else "CLP" for t in ok["instrument_type"]]
    ok["bucket"] = [
        B.tenor_bucket(B.years_between(a, bm_data.maturity_of(n, m) or a))
        for a, n, m in zip(ok["auction_date"], ok["nemo"], ok["maturity"], strict=True)
    ]
    q = ok.groupby(["quarter", "currency"])["allocated_usd"].sum().unstack(fill_value=0) / 1e6
    q = q.tail(8)
    fig = go.Figure()
    for cur, color in (("CLP", CAT[0]), ("UF", CAT[2])):
        if cur in q.columns:
            fig.add_trace(
                go.Bar(
                    x=q.index,
                    y=q[cur],
                    name=f"{cur} bonds",
                    marker=dict(color=color, line=dict(width=0)),
                )
            )
    base_layout(fig, "Issuance by quarter (US$ mm, allocated)", "US$ mm", 340)
    fig.update_layout(barmode="stack")
    a, b = st.columns([3, 2])
    a.plotly_chart(fig, width="stretch")
    y = date.today().year
    t = (
        (
            ok[ok["auction_date"].dt.year == y]
            .groupby(["bucket", "currency"])["allocated_usd"]
            .sum()
            .unstack(fill_value=0)
            / 1e6
        )
        .reindex(B.BUCKET_ORDER)
        .dropna(how="all")
    )
    b.markdown(f"**{y} year-to-date by tenor bucket (US$ mm)**")
    b.dataframe(text_table(t, "{:,.0f}"), width="stretch")


def cash_calendar() -> None:
    m, o = bm_data.master(), bm_data.outstanding()
    st.markdown("**Coupons and maturities, next 24 months** (cash returning to investors)")
    if m.empty or o.empty:
        pending("Bond master not loaded. Press Refresh on this sub-tab (weekly job).")
        return
    today = date.today()
    cf = B.cashflow_calendar(m, o, today, date(today.year + 2, today.month, 28))
    u, _ = B.asof_value(bm_data.uf(), today)
    cf["clp"] = [B.to_clp(a, unit, u) for a, unit in zip(cf["amount"], cf["unit"], strict=True)]
    cf["month"] = pd.to_datetime(cf["date"]).dt.to_period("M").dt.to_timestamp()
    g = cf.groupby(["month", "kind"])["clp"].sum().unstack(fill_value=0) / 1e9
    fig = go.Figure()
    for k, color in (("principal", CAT[1]), ("coupon", CAT[0])):
        if k in g.columns:
            fig.add_trace(
                go.Bar(
                    x=g.index,
                    y=g[k],
                    name=k.capitalize(),
                    marker=dict(color=color, line=dict(width=0)),
                )
            )
    base_layout(fig, None, "CLP bn", 340)
    fig.update_layout(barmode="stack")
    st.plotly_chart(fig, width="stretch")
    flagged = m[m["flagged"].fillna("").str.contains("exclude maturity month")]["nemo"].tolist()
    st.caption(
        f"On today's outstanding (Stock Total) and coupon months from Características "
        f"Financieras (inferred from the maturity month where not listed). UF converted at "
        f"today's UF {u:,.2f}. Hacienda lists coupon months that skip the maturity month for "
        f"{', '.join(flagged) or 'none'}: used as listed, flagged in Bond Master."
    )


def render() -> None:
    header(
        "Treasury issuance against the annual authorization, by currency and tenor, and the "
        "coupon/maturity calendar. Nominal amounts only.",
        "bm_sup",
        "master",
        "Bond master, outstanding and every consolidated file (weekly job)",
    )
    authorization()
    by_quarter()
    cash_calendar()
    st.markdown("**Exchange / buyback operations**")
    st.caption(
        "Pending: BCCh fiscal-agency exchange results are on bcentral.cl (bot-protected, "
        "manual download). Hacienda's 2022 file also has a 'Recompras' sheet, not loaded yet."
    )
    job_caption(["hacienda.soma", "hacienda.master"])
