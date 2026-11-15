"""Chile Macro · Surprises & Calendar."""

from __future__ import annotations

from datetime import datetime, timedelta

import cm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from cm_ui import header, pending

from monitor.models.macro_surprises import ACTIVITY, INFLATION, hit_rate, surprise_index

LABELS = {
    "cpi_mm": "CPI m/m (pp)",
    "imacec_yoy": "IMACEC y/y (pp)",
    "imacec_nm_yoy": "Non-mining IMACEC y/y (pp)",
}


def index_chart(s: pd.DataFrame, half_life: float, years: int) -> go.Figure:
    since = pd.Timestamp.today() - pd.DateOffset(years=years)
    fig = go.Figure()
    for inds, name, color in [
        (INFLATION, "Inflation", ROLE["headline"]),
        (ACTIVITY, "Activity", ROLE["eee"]),
    ]:
        idx = surprise_index(s, inds, half_life)
        idx = idx[idx.index >= since]
        line(fig, idx.index, idx.values, name, color)
    fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
    return base_layout(
        fig,
        f"Chile Macro Surprise Index (half-life {half_life:g}m; + = higher inflation / "
        "stronger activity)",
        "Σ weighted z",
        360,
    )


def calendar() -> None:
    r = cm_data.releases()
    st.markdown("#### Release calendar (next 60 days)")
    if r.empty:
        pending("INE calendar not loaded. Press Refresh.")
        return
    now = datetime.now()
    fut = r[(r["release_local"] >= now) & (r["release_local"] <= now + timedelta(days=60))].copy()
    last = cm_data.surprises()
    rows = []
    for x in fut.itertuples(index=False):
        ind = {"cpi": "cpi_mm"}.get(x.indicator)
        cons, last_s = "", ""
        if ind and not last.empty:
            sub = last[last["indicator"] == ind]
            if len(sub):
                last_s = f"{sub.iloc[-1].surprise:+.1f} ({sub.iloc[-1].reference_period})"
        if x.indicator == "cpi" and x.reference_period:
            from datetime import date as _d

            from monitor.models.inflation_nowcast import survey_for_month

            y_, m_ = map(int, x.reference_period.split("-"))
            v, src = survey_for_month(
                _d(y_, m_, 1),
                cm_data.series("eee_cpi_mm_this_month"),
                cm_data.series("eee_cpi_mm_next_month"),
                _d.today(),
            )
            cons = "" if v is None else f"{v:.1f}% (EEE {src})"
        rows.append(
            {
                "Release": x.title,
                "Santiago / NY": cm_data.fmt_release(x.release_local.to_pydatetime()),
                "Consensus": cons,
                "Last surprise": last_s,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Source: INE 'Agenda estadística' (scraped). BCCh releases (IMACEC, GDP, trade, "
        "current account): calendar on hold."
    )


def render() -> None:
    header(
        "Surprises vs the EEE median before each release. "
        "Source: BCCh BDE (series, EEE), INE agenda.",
        "cm_surprises",
    )
    s = cm_data.surprises()
    if s.empty:
        pending("No surprises computed yet. Press Refresh.")
        return
    c1, c2 = st.columns(2)
    hl = c1.slider("Half-life (months)", 1.0, 12.0, 3.0, 0.5, key="cm_hl")
    years = c2.slider("History (years)", 2, 20, 6, key="cm_si_years")
    st.plotly_chart(index_chart(s, hl, years), width="stretch")

    st.markdown("#### Release by release")
    view = s.sort_values("release_date", ascending=False).head(60).copy()
    view["Indicator"] = view["indicator"].map(LABELS)
    st.dataframe(
        view[
            [
                "release_date",
                "Indicator",
                "reference_period",
                "consensus",
                "actual",
                "surprise",
                "zscore",
                "actual_is_first_print",
            ]
        ]
        .rename(
            columns={
                "release_date": "Release (approx.)",
                "reference_period": "Reference",
                "consensus": "EEE",
                "actual": "Actual",
                "surprise": "Surprise",
                "zscore": "z",
                "actual_is_first_print": "First print?",
            }
        )
        .round({"EEE": 2, "Actual": 2, "Surprise": 2, "z": 2}),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Release dates are approximate (CPI ≈ 8th of the next month; IMACEC ≈ 1st two months "
        "later). z-scores use the std of earlier surprises only. **IMACEC historical surprises use "
        "revised data** (no real-time vintages available); first prints accumulate from now on. "
        "No unemployment surprises: the BDE has no unemployment consensus."
    )

    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        for ind, color in [("cpi_mm", ROLE["headline"]), ("imacec_yoy", ROLE["eee"])]:
            sub = s[s["indicator"] == ind]
            fig.add_trace(
                go.Histogram(
                    x=sub["surprise"],
                    name=LABELS[ind],
                    marker_color=color,
                    opacity=0.75,
                    xbins=dict(size=0.1 if ind == "cpi_mm" else 0.5),
                )
            )
        fig.update_layout(barmode="overlay", hovermode="closest")
        st.plotly_chart(base_layout(fig, "Surprise distribution", "count", 300), width="stretch")
    with c2:
        hr = hit_rate(s, 0.05).rename(index=LABELS)
        st.dataframe(
            hr.rename("Consensus exactly right").map("{:.0%}".format).to_frame(), width="stretch"
        )
        st.caption("Exactly right = |surprise| ≤ 0.05 (both are published to one decimal).")
    calendar()
