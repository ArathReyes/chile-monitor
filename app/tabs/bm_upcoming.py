"""Bond Monitor · Upcoming Auctions."""

from __future__ import annotations

from datetime import date, datetime

import bm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from bm_ui import countdown, header, job_caption, money, pending, text_table
from charts import CAT, base_layout

from monitor.models import bonds as B

WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BUCKET_COLOR = dict(zip(B.BUCKET_ORDER, CAT, strict=False))


def upcoming_table(cal: pd.DataFrame, now: datetime) -> pd.DataFrame:
    rows = []
    for r in cal.itertuples():
        o, c = bm_data.auction_window(r.auction_date.date())
        o_ny, c_ny = o.astimezone(bm_data.NY), c.astimezone(bm_data.NY)
        rows.append(
            {
                "Date": f"{r.auction_date:%d-%b-%y}",
                "Day": WEEKDAY[r.auction_date.weekday()],
                "Bidding (Santiago)": f"{o:%H:%M}–{c:%H:%M}",
                "Bidding (NY)": f"{o_ny:%H:%M}–{c_ny:%H:%M}",
                "Instrument": r.instrument,
                "Type": r.instrument_type,
                "Nemo": r.nemo,
                "Reopening": "yes" if r.reopening else "",
                "Size": f"{r.amount:,.0f} {r.unit}",
                "CLP": money(r.amount_clp, "$"),
                "USD": money(r.amount_usd, "US$"),
                "Countdown": countdown(o, now),
            }
        )
    return pd.DataFrame(rows)


def supply_chart(cal: pd.DataFrame) -> go.Figure:
    w = B.weekly_supply(cal, "amount_usd")
    fig = go.Figure()
    for bucket in B.BUCKET_ORDER:
        sub = w[w["bucket"] == bucket]
        if sub.empty:
            continue
        x = [[s for s in sub["segment"]], [f"{d:%d-%b}" for d in sub["week"]]]
        fig.add_trace(
            go.Bar(
                x=x,
                y=sub["amount_usd"] / 1e6,
                name=bucket,
                marker=dict(color=BUCKET_COLOR[bucket], line=dict(width=0)),
                hovertemplate=f"{bucket}: US$%{{y:,.0f}}mm<extra>%{{x}}</extra>",
            )
        )
    base_layout(fig, None, "US$ mm", 360)
    fig.update_layout(
        barmode="stack",
        hovermode="closest",
        xaxis=dict(categoryorder="array", type="multicategory"),
    )
    return fig


def render() -> None:
    header(
        "Next Treasury auctions (Hacienda calendar, current quarter). Dutch auction, bidding "
        "10:30–11:00 Santiago, settlement T+2. Sizes in nominal terms (no DV01).",
        "bm_up",
    )
    cal = bm_data.calendar()
    if cal.empty:
        pending("Auction calendar not loaded. Press Refresh (runs `monitor.jobs.auctions_daily`).")
        return
    now = datetime.now(bm_data.SCL)
    active = cal[cal["status"] == "active"]
    cal_usd = bm_data.with_usd(active, "amount", "auction_date")
    nxt = cal_usd[cal_usd["auction_date"].dt.date >= now.date()]
    if nxt.empty:
        st.info("No more auctions in the published calendar for this quarter.")
    else:
        st.dataframe(text_table(upcoming_table(nxt, now), {}), hide_index=True, width="stretch")
        fx = nxt["fx_date"].dropna()
        st.caption(
            f"USD at the dólar observado as of {max(fx):%d-%b-%Y} and UF of the same date "
            "(future dates use the latest published value)."
            if len(fx)
            else ""
        )
    st.markdown("**Weekly supply, nominal (US$ mm), by tenor bucket**")
    quarter_start = pd.Timestamp(date(now.year, 3 * ((now.month - 1) // 3) + 1, 1))
    st.plotly_chart(
        supply_chart(cal_usd[cal_usd["auction_date"] >= quarter_start]), width="stretch"
    )
    st.caption(
        "Tenor bucket = years from the auction date to maturity. Whole quarter shown "
        "(past weeks = auctions already held)."
    )
    st.markdown("**Calendar change log**")
    ch = bm_data.calendar_changes()
    if ch.empty:
        st.caption("No changes recorded yet.")
    else:
        show = ch.assign(
            detected=pd.to_datetime(ch["detected_at"]).dt.strftime("%d-%b-%y %H:%M UTC")
        )
        st.dataframe(
            text_table(
                show[
                    ["detected", "auction_date", "instrument", "change", "old_amount", "new_amount"]
                ],
                {"old_amount": "{:,.0f}", "new_amount": "{:,.0f}"},
            ),
            hide_index=True,
            width="stretch",
            height=260,
        )
        st.caption(
            "The first run records every auction as 'new'; later runs log new, resized "
            "(amount), removed and reinstated auctions."
        )
    job_caption(["hacienda.calendar"])
