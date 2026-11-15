"""Sub-tab: Decision History & Reaction Function."""

from __future__ import annotations

import mp_data
import plotly.graph_objects as go
import streamlit as st
from charts import DIVERGING, ROLE, base_layout, line
from mp_ui import pending, refresh_row, series_caption

from monitor.models.surprises import summary_stats, surprises_by_year, yoy


def _tpm_cpi_chart(tab) -> go.Figure:
    start = tab["decision_date"].min()
    tpm = mp_data.series("cl_tpm", start)
    cpi = mp_data.series("cl_cpi_yoy", start)
    core = yoy(mp_data.series("cl_cpi_core_spliced")).dropna()
    core = core[core.index >= str(start)]
    fig = go.Figure()
    fig.add_hrect(
        y0=2,
        y1=4,
        fillcolor="rgba(139,138,133,0.14)",
        line_width=0,
        annotation_text="target 3% ± 1",
        annotation_position="top left",
    )
    line(fig, tpm.index, tpm.values, "TPM", "#52514e", shape="hv")
    line(fig, cpi.index, cpi.values, "CPI y/y", ROLE["headline"])
    line(fig, core.index, core.values, "Core (SAE) y/y", ROLE["core"])
    moves = tab[tab["change_bp"] != 0]
    fig.add_trace(
        go.Scatter(
            x=moves["effective_date"],
            y=moves["tpm_after"],
            mode="markers",
            name="Rate decision",
            marker=dict(size=7, color="#52514e", line=dict(width=1.5, color="white")),
        )
    )
    return base_layout(fig, "TPM vs CPI headline and core", "%", 400)


def _surprise_chart(tab) -> go.Figure:
    t = tab.dropna(subset=["surprise_bp"])
    colors = [
        DIVERGING["pos"] if v > 0 else DIVERGING["neg"] if v < 0 else DIVERGING["mid"]
        for v in t["surprise_bp"]
    ]
    fig = go.Figure(
        go.Bar(
            x=t["decision_date"],
            y=t["surprise_bp"],
            marker_color=colors,
            name="Surprise vs survey",
            hovertemplate="%{x}: %{y:+.0f}bp",
        )
    )
    fig.update_layout(showlegend=False)
    return base_layout(fig, "Surprise vs survey (actual − expected, bp; + = hawkish)", "bp", 300)


def fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{v:.0%}" if pct else f"{v:+.0f}bp"


def render() -> None:
    top, btn = st.columns([5, 1])
    with btn:
        refresh_row("mp_history", markets=False)
    tab = mp_data.decision_table()
    if tab.empty:
        st.info("No decisions yet. Press Refresh.")
        return
    n_mt = int(tab["from_meetings"].sum())
    st.caption(
        f"{len(tab)} decisions since {tab['decision_date'].min():%Y}. Derived from TPM "
        f"changes (rate moves only; decision = business day before the effective date) "
        f"plus {n_mt} meeting(s) from meetings.yaml. Hold meetings appear once the full "
        "meeting calendar is configured."
    )

    st.plotly_chart(_tpm_cpi_chart(tab), width="stretch")
    series_caption(["cl_tpm", "cl_cpi_yoy", "cl_cpi_core_spliced"])

    s = summary_stats(tab)
    c = st.columns(6)
    c[0].metric("Hit rate (|surprise| < 12.5bp)", fmt(s["hit_rate"], True))
    c[1].metric(
        "Avg |surprise|",
        "n/a" if s["avg_abs_surprise_bp"] is None else f"{s['avg_abs_surprise_bp']:.1f}bp",
    )
    c[2].metric("Moves of 25bp", fmt(s["share_25"], True))
    c[3].metric(
        "Moves of 50bp / >50bp", f"{fmt(s['share_50'], True)} / {fmt(s['share_gt50'], True)}"
    )
    c[4].metric("Avg hike", fmt(s["avg_hike_bp"]))
    c[5].metric("Avg cut", fmt(s["avg_cut_bp"]))

    st.plotly_chart(_surprise_chart(tab), width="stretch")
    series_caption(["eof_tpm_next", "eee_tpm_this_month"], "BCCh BDE (EOF / EEE)")

    st.markdown("#### Decisions")
    view = tab[
        [
            "decision_date",
            "change_bp",
            "tpm_after",
            "vote",
            "expected_source",
            "expected_change_bp",
            "surprise_bp",
            "market_expected_bp",
            "cpi_yoy",
            "core_yoy",
            "imacec_yoy",
        ]
    ].sort_values("decision_date", ascending=False)
    st.dataframe(
        view.rename(
            columns={
                "decision_date": "Decision",
                "change_bp": "Δ bp",
                "tpm_after": "New TPM",
                "vote": "Vote",
                "expected_source": "Survey",
                "expected_change_bp": "Expected Δ bp",
                "surprise_bp": "Surprise bp",
                "market_expected_bp": "SPC90 implied Δ bp (approx)",
                "cpi_yoy": "CPI y/y",
                "core_yoy": "Core y/y",
                "imacec_yoy": "IMACEC y/y",
            }
        ).round(2),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "Expectations use only data published before each decision (EOF round within 45 "
        "days; else EEE). SPC90-implied Δ assumes one move in the 90-day window, so it "
        "overstates moves when two meetings fall inside it. CPI/core/IMACEC: latest "
        "print plausibly published by the decision date."
    )
    with st.expander("Surprises by year"):
        st.dataframe(surprises_by_year(tab).round(2), hide_index=True, width="stretch")
    pending(
        "Vote split, dissents and surprises by board president: pending statement/minutes "
        "documents and board.yaml."
    )
