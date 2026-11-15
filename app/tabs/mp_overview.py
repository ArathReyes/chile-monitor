"""Sub-tab: Overview."""

from __future__ import annotations

from datetime import date

import mp_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from mp_ui import refresh_row, series_caption


def eof_for_next_meeting(fi) -> tuple[float | None, str | None]:
    """EOF answer for the next meeting, shifted by meetings held since the survey round."""
    from monitor.models.tpm_implied import EOF_MEETING_KEYS, meetings_between

    if fi.survey_date is None:
        return None, None
    held = meetings_between(mp_data.meetings(), fi.survey_date, date.today())
    if held >= len(EOF_MEETING_KEYS):
        return None, None
    key = EOF_MEETING_KEYS[held]
    return fi.eof_latest.get(key), key


def _kpis(fit, fi, summary, tab) -> None:
    nxt, nxt_id = mp_data.next_meeting()
    c = st.columns(6)
    c[0].metric("TPM", f"{fi.tpm0:.2f}%", help=f"BCCh BDE, as of {fi.tpm_date}")
    if nxt:
        c[1].metric(
            "Next RPM",
            f"{nxt:%d %b %Y}",
            f"in {mp_data.days_until(nxt)} days",
            delta_color="off",
            help="Date from meetings.yaml (market-rule sourced, unconfirmed); "
            "decision time not configured",
        )
    else:
        c[1].metric("Next RPM", "not configured")
    k = summary[(summary.venue == "kalshi")] if not summary.empty else summary
    if not k.empty:
        r = k.iloc[0]
        probs, _, _ = mp_data.market_outcomes()
        g = probs[(probs.venue == "kalshi") & (probs.event == r.event)].set_index("change_bp")[
            "prob"
        ]
        top = g.sort_values(ascending=False).head(2)
        lbl = {-50: "cut>25", -25: "cut 25", 0: "hold", 25: "hike 25", 50: "hike>25"}
        c[2].metric(
            "Kalshi (next)",
            " / ".join(f"{lbl[i]} {p:.0%}" for i, p in top.items()),
            f"E[Δ] {r.expected_change_bp:+.1f}bp",
            delta_color="off",
        )
    else:
        c[2].metric("Kalshi (next)", "n/a")
    first = fit.path.iloc[0]
    c[3].metric(
        "SPC-implied (next)",
        f"{first.change_bp:+.1f}bp",
        f"{first.implied_tpm:.2f}% after",
        delta_color="off",
    )
    eof, key = eof_for_next_meeting(fi)
    c[4].metric(
        "EOF median (next)",
        f"{eof:.2f}%" if eof is not None else "n/a",
        f"survey {fi.survey_date}" + (f" · '{key}' answer" if key and key != "next" else ""),
        delta_color="off",
        help="The round's answer for the next meeting; shifted when a meeting has been held "
        "since the survey (e.g. the '1 reunión adelante' answer).",
    )
    past = tab[tab["decision_date"] < date.today()]
    if not past.empty:
        last = past.iloc[-1]
        vote = (last.vote or "vote n/a").split(" (")[0]
        c[5].metric(
            "Last decision",
            f"{last.change_bp:+.0f}bp",
            f"{last.decision_date:%d %b %Y} · {vote}",
            delta_color="off",
            help=last.vote or "Vote from the BCCh press release",
        )


def _compact_chart(fit, fi) -> go.Figure:
    p = fit.path
    fig = go.Figure()
    line(
        fig,
        [fi.curve_date, *p["effective_date"]],
        [fi.tpm0, *p["implied_tpm"]],
        "SPC-implied TPM",
        ROLE["market"],
        shape="hv",
    )
    tpm = mp_data.series("cl_tpm", date(date.today().year - 2, 1, 1))
    line(fig, tpm.index, tpm.values, "TPM (actual)", "#52514e", shape="hv")
    return base_layout(fig, "TPM: last 2 years and market-implied path", "%", 300)


def _what_changed(tab, fit) -> None:
    past = tab[tab["decision_date"] < date.today()]
    if past.empty:
        return
    last_dec = past.iloc[-1].decision_date
    try:
        then, fi_then, _ = mp_data.implied_fit(as_of=last_dec)
    except Exception as e:  # noqa: BLE001
        st.caption(f"Path as of last meeting unavailable: {e}")
        return
    now_p = fit.path.set_index("effective_date")["implied_tpm"]
    then_p = then.path.set_index("effective_date")["implied_tpm"]
    common = now_p.index.intersection(then_p.index)
    eof_now = mp_data.series("eof_tpm_next")
    eof_then = eof_now.loc[: pd.Timestamp(last_dec)]
    c = st.columns(4)
    tones = mp_data.statement_tones()
    if not tones.empty:
        lt = tones.iloc[-1]
        prev = tones.iloc[-2]["tone"] if len(tones) > 1 else None
        delta = f"{lt.tone - prev:+g} vs previous" if prev is not None else ""
        c[3].metric(
            "Statement tone (EOF)",
            f"{lt.tone:g}/10 · {lt.tone_label}",
            delta,
            delta_color="off",
            help=f"{lt.meeting_date:%d %b %Y} statement; 0 dovish, "
            "5 neutral, 10 hawkish (EOF round after the meeting)",
        )
    if len(common):
        d = (now_p[common] - then_p[common]) * 100
        c[0].metric(
            "Path shift since last meeting",
            f"{d.mean():+.1f}bp avg",
            f"{len(common)} common steps, curve {fi_then.curve_date} → now",
            delta_color="off",
        )
    else:
        c[0].metric(
            "Path shift since last meeting",
            "n/a",
            "no common meeting steps (dates differ)",
            delta_color="off",
        )
    if len(eof_then) and len(eof_now):
        c[1].metric(
            "EOF next-RPM shift",
            f"{(eof_now.iloc[-1] - eof_then.iloc[-1]) * 100:+.0f}bp",
            f"{eof_then.index[-1]:%d %b} → {eof_now.index[-1]:%d %b}",
            delta_color="off",
        )
    stm = mp_data.statements()
    if len(stm) >= 2:
        from monitor.models.statements import normalize
        from monitor.models.text_analysis import diff_stats

        ds = diff_stats(normalize(stm.iloc[-2]["text"]), normalize(stm.iloc[-1]["text"]))
        c[2].metric(
            "Statement change",
            f"{ds['share_changed']:.0%} of words",
            f"{stm.iloc[-2]['meeting_date']:%d %b} → {stm.iloc[-1]['meeting_date']:%d %b}",
            delta_color="off",
            help="Word-level diff; see Board & Communication",
        )
    else:
        c[2].metric("Statement change", "n/a", "no statements loaded", delta_color="off")
    st.caption(
        f"Last meeting used: {last_dec} "
        f"(from {'meetings.yaml' if past.iloc[-1].from_meetings else 'TPM changes'})."
    )


def render() -> None:
    top, btn = st.columns([5, 1])
    with btn:
        refresh_row("mp_overview")
    try:
        fit, fi, _ = mp_data.implied_fit()
    except Exception as e:  # noqa: BLE001
        st.error(f"Data not available yet ({e}). Press Refresh.")
        return
    _, summary, _ = mp_data.market_outcomes()
    tab = mp_data.decision_table()
    _kpis(fit, fi, summary, tab)
    st.plotly_chart(_compact_chart(fit, fi), width="stretch")
    series_caption(["cl_tpm", "cl_spc_nom_90d", "cl_spc_nom_360d"])
    st.caption(
        "Full path, probabilities and diagnostics: **Implied Path & Probabilities** sub-tab."
    )
    st.markdown("#### What changed since the last meeting")
    _what_changed(tab, fit)
    st.caption(
        "Board stances (from statement dissents), vote record and statement diffs: "
        "**Board & Communication** sub-tab."
    )
