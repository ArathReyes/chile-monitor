"""Chile Macro · Seasonality & Nowcast."""

from __future__ import annotations

from datetime import date

import cm_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout
from cm_ui import header, series_caption

from monitor.models.inflation_nowcast import DISAGREE_BP, implied_12m, next_months, nowcast_table
from monitor.models.seasonality import heatmap_table

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SCENARIOS = {
    "seasonal_avg": "Seasonal average",
    "last_year": "Last year",
    "eee": "EEE median",
    "eof": "EOF median",
}


def controls() -> tuple[int, tuple[int, ...]]:
    c1, c2 = st.columns([2, 1])
    window = c1.slider("Seasonal window (years)", 5, 25, 10, key="cm_season_window")
    excl = c2.toggle("Exclude 2021–2022 (outlier years)", True, key="cm_season_excl")
    return window, ((2021, 2022) if excl else ())


def heatmap() -> go.Figure:
    h = heatmap_table(cm_data.series("cl_cpi_mm_hist"), since_year=date.today().year - 15)
    fig = go.Figure(
        go.Heatmap(
            z=h.values,
            x=MONTHS,
            y=h.index.astype(str),
            zmid=0.25,
            colorscale=[[0, "#2a78d6"], [0.5, "#f0efec"], [1, "#e34948"]],
            colorbar=dict(title="% m/m"),
            hovertemplate="%{y} %{x}: %{z:.1f}%<extra></extra>",
            text=h.round(1).values,
            texttemplate="%{text}",
            textfont=dict(size=10),
        )
    )
    fig.update_layout(yaxis=dict(autorange="reversed"), hovermode="closest")
    return base_layout(
        fig, "CPI m/m by year × month (INE official; midpoint 0.25% ≈ 3% a year)", "", 460
    )


def profile(st_df: pd.DataFrame, window: int) -> go.Figure:
    mm = cm_data.series("cl_cpi_mm_hist")
    cur = mm[mm.index.year == date.today().year]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=MONTHS, y=st_df["max"], line=dict(width=0), showlegend=False, hoverinfo="skip")
    )
    fig.add_trace(
        go.Scatter(
            x=MONTHS,
            y=st_df["min"],
            fill="tonexty",
            line=dict(width=0),
            fillcolor="rgba(42,120,214,0.15)",
            name="min–max",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=MONTHS,
            y=st_df["mean"],
            name="Mean",
            mode="lines+markers",
            line=dict(color=ROLE["market"], width=2),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=MONTHS,
            y=st_df["median"],
            name="Median",
            mode="lines",
            line=dict(color=ROLE["market"], width=2, dash="dot"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[MONTHS[m - 1] for m in cur.index.month],
            y=cur.values,
            name=str(date.today().year),
            mode="markers",
            marker=dict(size=11, color=ROLE["eof"], line=dict(width=2, color="white")),
        )
    )
    return base_layout(fig, f"Seasonal profile ({window}-year window)", "% m/m", 340)


def nowcast(st_df: pd.DataFrame) -> pd.DataFrame:
    last = cm_data.last_cpi_month()
    targets = next_months(last, 3)
    return nowcast_table(
        targets,
        cm_data.series("cl_cpi_mm_hist"),
        st_df,
        cm_data.series("eee_cpi_mm_this_month"),
        cm_data.series("eee_cpi_mm_next_month"),
        cm_data.series("eof_cpi_mm_this_month"),
        cm_data.series("eof_cpi_mm_next_month"),
        cm_data.series("eof_cpi_mm_2m"),
        date.today(),
    )


def scenario_dict(table: pd.DataFrame, col: str, st_df: pd.DataFrame) -> dict[date, float]:
    """Scenario values per month; months without a value use the seasonal mean."""
    out = {}
    for r in table.itertuples(index=False):
        v = getattr(r, col)
        out[r.month] = (
            float(v) if v is not None and v == v else float(st_df.loc[r.month.month, "mean"])
        )
    return out


def paths(table: pd.DataFrame, st_df: pd.DataFrame) -> pd.DataFrame:
    hist = cm_data.series("cl_cpi_mm_hist")
    fill = st_df["mean"].to_dict()
    t = date.today()
    dec = date(t.year if t.month < 12 else t.year + 1, 12, 1)
    ahead = (pd.Timestamp(t.year, t.month, 1) + pd.DateOffset(months=11)).date()
    rows = []
    for col, name in SCENARIOS.items():
        sc = scenario_dict(table, col, st_df)
        rows.append(
            {
                "Scenario": name,
                f"12m to {dec:%b-%y}": implied_12m(hist, sc, fill, dec),
                f"12m to {ahead:%b-%y} (11m ahead)": implied_12m(hist, sc, fill, ahead),
            }
        )
    return pd.DataFrame(rows)


def render() -> None:
    header(
        "CPI seasonality and a nowcast for the next 3 months. "
        "Source: BCCh BDE (INE CPI, EEE, EOF).",
        "cm_season",
    )
    if cm_data.last_cpi_month() is None:
        from cm_ui import pending

        pending("CPI data not loaded. Press Refresh.")
        return
    window, excl = controls()
    st_df = cm_data.seasonal(window, excl)
    c1, c2 = st.columns([3, 2])
    with c1:
        st.plotly_chart(heatmap(), width="stretch")
    with c2:
        st.plotly_chart(profile(st_df, window), width="stretch")
        st.dataframe(
            st_df.round(2).rename(index=dict(enumerate(MONTHS, start=1))).T, width="stretch"
        )
    series_caption(["cl_cpi_mm_hist"])

    st.markdown("#### Nowcast: next 3 months (m/m %)")
    table = nowcast(st_df)
    view = table.rename(
        columns={
            "month": "Month",
            "seasonal_avg": "Seasonal avg",
            "last_year": "Last year",
            "eee": "EEE median",
            "eof": "EOF median",
            "market": "Market-implied",
            "spread_bp": "Max−min (bp)",
            "disagree": f"> {DISAGREE_BP:.0f}bp?",
        }
    )
    view["Market-implied"] = "n/a"
    st.dataframe(
        view.style.format(precision=2, na_rep="–").apply(
            lambda r: [
                "background-color: rgba(236,131,90,0.18)" if r[f"> {DISAGREE_BP:.0f}bp?"] else ""
                for _ in r
            ],
            axis=1,
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "EEE: 'en el mes' answers for the survey month, 'mes siguiente' for the next (survey "
        "counted from ~12 days into its month). EOF: latest round answering for each month. "
        "Market-implied: n/a — there are no public short-end UF swaps, and it isn't modeled. "
        "Rows with > 10bp disagreement are highlighted."
    )

    st.markdown("#### Implied 12-month inflation")
    p = paths(table, st_df)
    c1, c2 = st.columns([3, 2])
    with c1:
        st.dataframe(p.round(2), hide_index=True, width="stretch")
        st.caption(
            "Published months are actual; the 3 nowcast months use each scenario; later months "
            "use the seasonal average. Target 3%, band 2–4%."
        )
    with c2:
        refs = []
        for sid, name in [
            ("eee_cpi_yoy_11m", "EEE: 12m in 11 months"),
            (f"eee_cpi_dec{date.today().year}", f"EEE: Dec {date.today().year}"),
            ("cl_bei_swap_1y", "Swap breakeven 1y (BCCh)"),
        ]:
            try:
                s = cm_data.series(sid)
            except KeyError:
                continue
            if len(s):
                refs.append(
                    {
                        "Reference": name,
                        "Value %": round(float(s.iloc[-1]), 2),
                        "As of": s.index[-1].date(),
                    }
                )
        st.dataframe(pd.DataFrame(refs), hide_index=True, width="stretch")
