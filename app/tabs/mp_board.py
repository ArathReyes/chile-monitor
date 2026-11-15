"""Sub-tab: Board & Communication."""

from __future__ import annotations

from datetime import date

import mp_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from components import source_badge
from mp_ui import pending, series_caption

from monitor.models.board import member_stance, member_votes, vote_record
from monitor.models.text_analysis import diff_html, diff_stats

# Fixed categorical slots per member (first appearance order), never cycled.
MEMBER_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
STANCE_ICON = {
    "Hawkish-leaning": "🦅",
    "Dovish-leaning": "🕊️",
    "Mixed": "⚖️",
    "No recorded dissents": "🤝",
}


def _cards(stance: pd.DataFrame) -> None:
    cols = st.columns(min(len(stance), 3) or 1)
    for i, r in enumerate(stance.itertuples(index=False)):
        with cols[i % len(cols)].container(border=True):
            st.markdown(f"**{r.member}** · {r.last_role}")
            st.caption(
                "Photo, full name, education and term: pending board pages "
                "(data/manual/bcch_web/board/)."
            )
            st.markdown(f"{STANCE_ICON[r.stance]} **{r.stance}**")
            if r.dissents:
                st.caption(
                    f"{r.dissents} dissent(s) in {r.named_votes} named votes · "
                    f"{r.hawkish} hawkish / {r.dovish} dovish · net {r.net_gap_bp:+.0f}bp"
                )
            else:
                st.caption(f"Voted with the majority in all {r.named_votes} named split votes.")
            st.caption(f"Named in statements {r.first_seen:%b %Y} – {r.last_seen:%b %Y}")


def _dissent_chart(mv: pd.DataFrame) -> go.Figure | None:
    d = mv[mv["dissent"]]
    if d.empty:
        return None
    order = list(dict.fromkeys(d.sort_values("meeting_date")["member"]))
    fig = go.Figure()
    for i, m in enumerate(order):
        g = d[d["member"] == m]
        fig.add_trace(
            go.Scatter(
                x=g["meeting_date"],
                y=g["gap_bp"],
                mode="markers",
                name=m,
                marker=dict(size=12, color=MEMBER_COLORS[i % 3], line=dict(width=2, color="white")),
                customdata=g[["decided_bp", "preferred_bp"]],
                hovertemplate=f"{m}<br>%{{x}}<br>decided %{{customdata[0]:+d}}bp, "
                "wanted %{customdata[1]:+d}bp<extra></extra>",
            )
        )
    fig.add_hline(y=0, line=dict(color="#8b8a85", width=1))
    fig.update_layout(hovermode="closest")
    return base_layout(fig, "Dissents: preferred − decided (bp; + = hawkish)", "bp", 300)


def _archive(stm: pd.DataFrame) -> None:
    labels = [
        f"{r.meeting_date:%d %b %Y} · {r.parsed.decision.change_bp:+d}bp → "
        f"{r.parsed.decision.new_rate:g}%"
        for r in stm.itertuples()
    ]
    idx = st.selectbox(
        "Meeting",
        range(len(stm)),
        index=len(stm) - 1,
        format_func=lambda i: labels[i],
        key="mp_stmt_sel",
    )
    cur = stm.iloc[idx]
    from monitor.models.statements import normalize

    t_cur = normalize(cur["text"])
    if idx > 0:
        prev = stm.iloc[idx - 1]
        t_prev = normalize(prev["text"])
        ds = diff_stats(t_prev, t_cur)
        st.caption(
            f"vs {prev['meeting_date']:%d %b %Y}: +{ds['words_added']} / "
            f"−{ds['words_removed']} words ({ds['share_changed']:.0%} changed)"
        )
        view = st.radio(
            "View",
            ["Diff vs previous", "Full text"],
            horizontal=True,
            key="mp_stmt_view",
            label_visibility="collapsed",
        )
        if view.startswith("Diff"):
            st.markdown(
                f'<div style="line-height:1.6">{diff_html(t_prev, t_cur)}</div>',
                unsafe_allow_html=True,
            )
            return
    st.write(t_cur)


def render() -> None:
    stm = mp_data.statements()
    if stm.empty:
        pending(
            "No statements ingested yet. Save them into data/manual/bcch_web/statements/ "
            "and run `uv run python -m monitor.jobs.mp_daily`."
        )
        return
    parsed = list(stm["parsed"])
    mv = member_votes(parsed)
    stance = member_stance(mv)
    rec = vote_record(parsed)
    asof = f"{stm['meeting_date'].min():%b %Y} – {stm['meeting_date'].max():%b %Y}"

    st.markdown("#### Board members (revealed stance from statement dissents)")
    st.caption(
        f"Based on {len(rec)} statements ({asof}); {int((~rec['unanimous']).sum())} "
        "had dissents. Unanimous statements don't name members, so stance only reflects "
        "meetings with a split vote, and current members who never dissented show no lean. "
        "Current composition, photos and education need the saved board pages."
    )
    _cards(stance)

    fig = _dissent_chart(mv)
    if fig:
        st.plotly_chart(fig, width="stretch")

    st.markdown("#### Vote record")
    view = (
        rec.sort_values("meeting_date", ascending=False)
        .rename(
            columns={
                "meeting_date": "Meeting",
                "change_bp": "Δ bp",
                "new_rate": "TPM",
                "split": "Vote",
                "dissent_summary": "Dissents (preferred move)",
            }
        )
        .drop(columns=["unanimous", "meeting_id"])
    )
    st.dataframe(view, hide_index=True, width="stretch")
    source_badge("BCCh press releases (saved manually), parsed", None)

    st.markdown("#### Statement archive")
    _archive(stm)

    st.markdown("#### Minutes")
    pending(
        "Minutes not loaded. Not needed for votes (statements name dissenters); they'd add "
        "the dissent rationale and the policy options discussed."
    )

    st.markdown("#### Market-perceived statement bias (EOF)")
    s = mp_data.series("eof_statement_bias", date(2021, 1, 1))
    if not s.empty:
        f2 = go.Figure()
        line(
            f2, s.index, s.values, "EOF: perceived bias of BCCh statements", ROLE["eof"], shape="hv"
        )
        f2.update_traces(mode="lines+markers")
        st.plotly_chart(
            base_layout(f2, "EOF: 'Percepción del sesgo Comunicados BCCh'", "index", 260),
            width="stretch",
        )
        series_caption(["eof_statement_bias"])
        st.caption(
            f"⚠️ Scale/direction unconfirmed (range {s.min():g}–{s.max():g}); not used as hawk/dove."
        )
