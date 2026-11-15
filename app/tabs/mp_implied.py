"""Sub-tab: Implied Path & Probabilities."""

from __future__ import annotations

from datetime import date

import mp_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import INK, ROLE, base_layout, line, markers
from components import source_badge
from mp_ui import pending, refresh_row, series_caption

DEFAULTS = {"mp_lam": 0.005, "mp_mu": 0.25, "mp_anchor": True, "mp_2y": False, "mp_w2y": 0.1}


def _state(k):
    return st.session_state.get(k, DEFAULTS[k])


def _main_chart(fit, fi, prior, band, summary) -> go.Figure:
    p = fit.path
    x = [fi.curve_date, *p["effective_date"]]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=band.index, y=band["hi"], line=dict(width=0), showlegend=False, hoverinfo="skip"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=band.index,
            y=band["lo"],
            fill="tonexty",
            line=dict(width=0),
            fillcolor="rgba(42,120,214,0.15)",
            name="λ/μ sensitivity",
            hoverinfo="skip",
        )
    )
    line(fig, x, [fi.tpm0, *p["implied_tpm"]], "SPC-implied TPM", ROLE["market"], shape="hv")
    line(fig, x, [fi.tpm0, *prior], "EOF median path (prior)", ROLE["eof"], shape="hv", dash="dot")
    eee = mp_data.wide(tuple(mp_data.EEE_IDS))
    if not eee.dropna(how="all").empty:
        last = eee.dropna(how="all").iloc[-1]
        sd = eee.dropna(how="all").index[-1]
        pts = [
            (sd + pd.DateOffset(months=m), last[f"eee_tpm_{k}"])
            for k, m in mp_data.EEE_HORIZONS.items()
            if pd.notna(last[f"eee_tpm_{k}"])
        ]
        markers(
            fig,
            [d for d, _ in pts],
            [v for _, v in pts],
            f"EEE median ({sd:%b-%y})",
            ROLE["eee"],
            symbol="diamond",
        )
    if not summary.empty:
        for venue in ("kalshi", "polymarket"):
            s = summary[summary.venue == venue]
            if s.empty:
                continue
            markers(
                fig,
                s["meeting_date"],
                fi.tpm0 + s["expected_change_bp"] / 100,
                f"{venue.title()} expected (vs current TPM)",
                ROLE[venue],
                symbol="square" if venue == "kalshi" else "triangle-up",
                customdata=s[["quality", "overround"]],
                hovertemplate="%{y:.3f}% · %{customdata[0]} · overround %{customdata[1]:.2f}",
            )
    fig.add_hline(
        y=fi.tpm0,
        line=dict(color=INK, width=1, dash="dash"),
        annotation_text=f"TPM {fi.tpm0:.2f}%",
        annotation_position="bottom left",
    )
    return base_layout(fig, "Market vs Survey vs BCCh", "TPM, %", 430)


def _meeting_table(fit, fi, summary, prior) -> pd.DataFrame:
    p = fit.path.copy()
    t = pd.DataFrame(
        {
            "Meeting": [
                lbl if s else f"{lbl} (grid)"
                for lbl, s in zip(p["label"], p["scheduled"], strict=True)
            ],
            "Decision": p["decision_date"],
            "Days": [(d - date.today()).days for d in p["decision_date"]],
            "Implied TPM": p["implied_tpm"].round(3),
            "Δ bp": p["change_bp"].round(1),
            "Cum bp": p["cum_change_bp"].round(1),
            "Cut 50": p["p_-50"],
            "Cut 25": p["p_-25"],
            "Hold": p["p_+0"],
            "Hike 25": p["p_+25"],
            "Hike 50": p["p_+50"],
            "Survey (EOF)": prior.round(3),
            "Mkt − survey bp": ((p["implied_tpm"] - prior) * 100).round(1),
        }
    )
    for venue in ("kalshi", "polymarket"):
        s = summary[summary.venue == venue] if not summary.empty else summary
        m = {} if s.empty else dict(zip(s["meeting_date"], s["expected_change_bp"], strict=True))
        t[f"{venue.title()} E[Δ] bp"] = [
            round(m[d], 1) if d in m else None for d in p["decision_date"]
        ]
    return t


def _markets_panel(probs, summary, ts):
    if probs.empty:
        pending("No prediction-market snapshot yet. Press Refresh.")
        return
    for (venue, event), g in probs.groupby(["venue", "event"], sort=False):
        srow = summary[(summary.venue == venue) & (summary.event == event)].iloc[0]
        flag = "" if srow.quality == "ok" else " · ⚠ illiquid (wide spreads / overround)"
        st.markdown(
            f"**{venue.title()} · {srow.meeting_date:%d %b %Y}** · overround "
            f"{srow.overround:.2f} · E[Δ] {srow.expected_change_bp:+.1f}bp{flag}"
        )
        st.dataframe(
            g[["outcome", "change_bp", "yes_bid", "yes_ask", "mid", "prob"]].rename(
                columns={
                    "change_bp": "Δ bp",
                    "yes_bid": "bid",
                    "yes_ask": "ask",
                    "prob": "prob (norm.)",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "prob (norm.)": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=1
                )
            },
        )
    source_badge("Kalshi Trade API v2 / Polymarket Gamma API (snapshots)", ts)


def _kalshi_history():
    h = mp_data.kalshi_history()
    if h.empty:
        return
    ev = sorted(h["event"].unique())[-1]
    g = h[h["event"] == ev]
    fig = go.Figure()
    colors = {
        "HOLD": INK,
        "H25": ROLE["eof"],
        "H25P": "#e34948",
        "C25": ROLE["market"],
        "C25P": "#6250d6",
    }
    names = {
        "HOLD": "Hold",
        "H25": "Hike 1-25",
        "H25P": "Hike >25",
        "C25": "Cut 1-25",
        "C25P": "Cut >25",
    }
    for code in ["C25P", "C25", "HOLD", "H25", "H25P"]:
        d = g[g["outcome"] == code].sort_values("date")
        if not d.empty:
            line(fig, d["date"], d["prob"] * 100, names[code], colors[code])
    base_layout(fig, f"Kalshi {ev}: implied probability history (daily candles)", "%", 320)
    st.plotly_chart(fig, width="stretch")


def render() -> None:
    top, btn = st.columns([5, 1])
    with top:
        st.caption(
            "**SPC data is end-of-day, T-1 (BCCh BDE). Kalshi / Polymarket are snapshots "
            "(refresh for latest).**"
        )
    with btn:
        refresh_row("mp_implied")
    venues = ("kalshi",) if _state("mp_anchor") else ()
    try:
        fit, fi, prior = mp_data.implied_fit(
            _state("mp_lam"), _state("mp_mu"), venues, 4.0, _state("mp_2y"), _state("mp_w2y")
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Implied path unavailable: {e}")
        return
    band = mp_data.sensitivity_band(
        _state("mp_lam"), _state("mp_mu"), _state("mp_2y"), _state("mp_w2y")
    )
    probs, summary, ts = mp_data.market_outcomes()

    q_icon = {"good": "🟢", "fair": "🟡", "poor": "🔴"}[fit.quality]
    st.markdown(
        f"Fit quality: {q_icon} **{fit.quality}** (RMSE {fit.rmse_bp:.1f}bp) · "
        f"curve date {fi.curve_date} · TPM {fi.tpm0:.2f}% · spread TIB−TPM "
        f"{fi.spread_bp:+.1f}bp"
    )
    for n in fit.notes:
        st.caption(f"ℹ️ {n}")
    st.plotly_chart(_main_chart(fit, fi, prior, band, summary), width="stretch")
    series_caption(["cl_spc_nom_90d", "cl_spc_nom_180d", "cl_spc_nom_360d", "eof_tpm_next"])
    pending(
        "IPoM TPM corridor (p10/p50/p90): pending manual IPoM files (data/manual/bcch_web/ipom/)."
    )

    st.markdown("#### Per-meeting path")
    t = _meeting_table(fit, fi, summary, prior)
    pct = {
        c: st.column_config.NumberColumn(format="percent")
        for c in ["Cut 50", "Cut 25", "Hold", "Hike 25", "Hike 50"]
    }
    st.dataframe(t, hide_index=True, width="stretch", column_config=pct)
    st.caption(
        "SPC probabilities split each expected change between the two nearest 25bp "
        "outcomes (FedWatch-style); they are not a full distribution. Grid rows are "
        "unscheduled monthly steps used until meeting dates are configured."
    )

    st.markdown("#### Prediction markets")
    c1, c2 = st.columns([3, 2])
    with c1:
        _markets_panel(probs, summary, ts)
    with c2:
        _kalshi_history()

    with st.expander("Fit diagnostics"):
        c = st.columns(4)
        c[0].slider(
            "λ (survey prior)", 0.0, 0.2, DEFAULTS["mp_lam"], 0.005, key="mp_lam", format="%.3f"
        )
        c[1].slider("μ (smoothness)", 0.0, 2.0, DEFAULTS["mp_mu"], 0.05, key="mp_mu")
        c[2].toggle("Anchor meeting 1 to Kalshi", DEFAULTS["mp_anchor"], key="mp_anchor")
        c[3].toggle("Use SPC 2y (weak, flagged)", DEFAULTS["mp_2y"], key="mp_2y")
        if _state("mp_2y"):
            st.slider("2y weight", 0.0, 1.0, DEFAULTS["mp_w2y"], 0.05, key="mp_w2y")
            st.warning(
                "2y: semiannual ACT/360 coupons; the BDE quote (365 basis) is converted ×360/365. "
                "The path is flat after the last step, so the 2y mostly tests the tail."
            )
        r = fit.residuals.copy()
        r["quote_basis"] = r["quote_basis"].map(
            {360: "ACT/360 (traded, T-1)", 365: "365 (tenor table, close)"}
        )
        st.dataframe(r.round(4), hide_index=True, width="stretch")
        st.caption(
            "Model: TPM steps on effective dates (decision + 1 CL business day); overnight = "
            "TPM + spread; Cámara index compounds business-day rates over calendar days, "
            "ACT/360; swaps start on their quote date (spot lag assumed 0)."
        )
