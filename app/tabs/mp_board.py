"""Sub-tab: Board & Communication."""

from __future__ import annotations

from datetime import date

import mp_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import base_layout
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
    "No named votes in statements": "·",
}


def _avatar(name: str) -> None:
    initials = "".join(w[0] for w in name.split()[:2]).upper()
    st.markdown(
        f'<div style="width:96px;height:96px;border-radius:50%;background:rgba(139,138,133,0.25);'
        f"display:flex;align-items:center;justify-content:center;font-size:30px;"
        f'font-weight:600;color:#52514e">{initials}</div>',
        unsafe_allow_html=True,
    )


def _stance_line(r) -> None:
    label = r.stance if isinstance(r.stance, str) else "No named votes in statements"
    icon = STANCE_ICON.get(label, "·")
    st.markdown(f"{icon} **{label}**")
    if isinstance(r.dissents, (int, float)) and r.dissents and r.dissents == r.dissents:
        st.caption(
            f"{int(r.dissents)} dissent(s) in {int(r.named_votes)} named votes · "
            f"{int(r.hawkish)} hawkish / {int(r.dovish)} dovish · net {r.net_gap_bp:+.0f}bp"
        )
    elif label == "No recorded dissents":
        st.caption(f"Voted with the majority in all {int(r.named_votes)} split votes where named.")
    else:
        st.caption(
            "Not named in any split vote (all decisions in their term were unanimous "
            "or they joined after the last split)."
        )


def _cards(board: pd.DataFrame, profiles: list, photos: dict) -> None:
    from monitor.sources.board import match_profile

    cols = st.columns(min(len(board), 3) or 1)
    for i, r in enumerate(board.itertuples(index=False)):
        prof = match_profile(r.full_name, profiles)
        with cols[i % len(cols)].container(border=True):
            c1, c2 = st.columns([1, 2.4])
            with c1:
                if r.surname_key in photos:
                    st.image(photos[r.surname_key], width=96)
                else:
                    _avatar(r.full_name)
            with c2:
                st.markdown(f"**{r.full_name}**")
                role = (
                    r.role_now
                    if r.role_now == "Board member"
                    else f"{r.role_now} (since {r.role_since})"
                )
                yrs = r.years_on_board
                tenure = "joined this year" if yrs == 0 else f"{yrs} yr{'s' if yrs != 1 else ''}"
                st.caption(f"{role} · on the Board since {r.board_since} ({tenure})")
            if prof and prof.education:
                st.markdown("**Education**")
                for e in prof.education:
                    st.caption(e)
            else:
                st.caption("Education: profile page not saved yet.")
            _stance_line(r)
            if prof:
                with st.expander("Bio"):
                    st.write(prof.bio)


TONE_COLORS = {
    "Hawkish": "#e34948",
    "Mildly hawkish": "#ec835a",
    "Neutral": "#8b8a85",
    "Mildly dovish": "#6da7ec",
    "Dovish": "#2a78d6",
}


def _tone_chart() -> None:
    t = mp_data.statement_tones()
    if t.empty:
        st.caption("No EOF tone readings yet.")
        return
    fig = go.Figure()
    fig.add_hrect(y0=5, y1=10, fillcolor="rgba(227,73,72,0.06)", line_width=0)
    fig.add_hrect(y0=0, y1=5, fillcolor="rgba(42,120,214,0.06)", line_width=0)
    fig.add_hline(
        y=5,
        line=dict(color="#8b8a85", width=1, dash="dot"),
        annotation_text="neutral",
        annotation_position="top left",
    )
    fig.add_trace(
        go.Scatter(
            x=t["meeting_date"],
            y=t["tone"],
            mode="lines+markers",
            name="Statement tone",
            line=dict(color="#52514e", width=2),
            marker=dict(
                size=10,
                color=[TONE_COLORS[x] for x in t["tone_label"]],
                line=dict(width=2, color="white"),
            ),
            customdata=t[["tone_label", "eof_date"]],
            hovertemplate="Statement %{x}<br>tone %{y:g}/10 · %{customdata[0]}"
            "<br>EOF round %{customdata[1]}<extra></extra>",
        )
    )
    fig.update_layout(showlegend=False, hovermode="closest", yaxis_range=[0, 10])
    st.plotly_chart(
        base_layout(
            fig, "Tone of each BCCh statement (0 dovish · 5 neutral · 10 hawkish)", "score", 300
        ),
        width="stretch",
    )
    last = t.iloc[-1]
    st.caption(
        f"Latest: {last.meeting_date:%d %b %Y} statement scored {last.tone:g}/10 "
        f"({last.tone_label}). Source: EOF 'tone in the latest announcements made by the "
        "BCCh regarding the last MPM' (BDE F089.EOF.PS_TPM.CMA.D), median, from the round "
        "15–22 days after each meeting."
    )
    series_caption(["eof_statement_bias"])


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
    tn = mp_data.statement_tones().set_index("meeting_date")
    if cur["meeting_date"] in tn.index:
        r = tn.loc[cur["meeting_date"]]
        st.markdown(f"EOF tone: **{r['tone']:g}/10 · {r['tone_label']}** (round {r['eof_date']})")
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

    bd = mp_data.board_data()
    auth = bd["authorities"]
    st.markdown("#### Current Board")
    if auth.empty:
        err = f" ({bd['error']})" if bd["error"] else ""
        pending(
            "Board composition needs the 'Histórico Autoridades' PDF in "
            f"data/manual/bcch_web/board/.{err}"
        )
    else:
        from monitor.models.board import attach_stance, current_board, former_in_statements

        board = attach_stance(current_board(auth, date.today().year), stance, bd["aliases"])
        _cards(board, bd["profiles"], bd["photos"])
        st.caption(
            "Composition and terms: BCCh 'Nómina de autoridades' PDF (years only). Hawk/dove: "
            f"revealed stance from {len(rec)} press releases ({asof}), of which "
            f"{int((~rec['unanimous']).sum())} had dissents; unanimous statements don't name "
            "members, so no dissent doesn't mean neutral. Photos: saved manually (BCCh profiles)."
        )
        former = former_in_statements(stance, board, auth, bd["aliases"])
        if not former.empty:
            with st.expander(f"Former members named in statements ({len(former)})"):
                st.dataframe(former, hide_index=True, width="stretch")
                if (former["board_term"] == "not found in authorities list").any():
                    st.caption(
                        "⚠️ Names not found in the authorities PDF are shown as written "
                        "in the statements. Confirm aliases in config/board_overrides.yaml."
                    )

    fig = _dissent_chart(mv)
    if fig:
        st.plotly_chart(fig, width="stretch")

    st.markdown("#### Vote record")
    tones = mp_data.statement_tones().set_index("meeting_date")
    rec = rec.assign(
        tone=rec["meeting_date"].map(tones["tone"]),
        tone_label=rec["meeting_date"].map(tones["tone_label"]),
    )
    view = (
        rec.sort_values("meeting_date", ascending=False)
        .rename(
            columns={
                "meeting_date": "Meeting",
                "change_bp": "Δ bp",
                "new_rate": "TPM",
                "split": "Vote",
                "dissent_summary": "Dissents (preferred move)",
                "tone": "EOF tone (0–10)",
                "tone_label": "Tone",
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

    st.markdown("#### Statement tone (EOF: how traders read each statement)")
    _tone_chart()
