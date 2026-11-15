"""Sub-tab: Surveys (EEE / EOF)."""

from __future__ import annotations

from datetime import date, timedelta

import mp_data
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from mp_ui import pending, refresh_row, series_caption

EEE_LINES = [
    ("eee_tpm_this_month", "This month", "#52514e"),
    ("eee_tpm_5m", "5 months", ROLE["market"]),
    ("eee_tpm_11m", "11 months", ROLE["eof"]),
    ("eee_tpm_23m", "23 months", ROLE["eee"]),
]
EOF_LINES = [
    ("eof_tpm_next", "Next RPM", "#52514e"),
    ("eof_tpm_rpm2_3m", "2 meetings ahead (3m)", ROLE["market"]),
    ("eof_tpm_rpm4_6m", "4 meetings ahead (6m)", ROLE["eof"]),
    ("eof_tpm_12m", "12 months", ROLE["eee"]),
]


def _lines(spec, start, title) -> go.Figure:
    fig = go.Figure()
    for sid, name, color in spec:
        s = mp_data.series(sid, start)
        line(fig, s.index, s.values, name, color, shape="hv")
    return base_layout(fig, title, "TPM, %", 340)


def eof_shift_after_meetings(tab: pd.DataFrame, window: int = 25) -> pd.DataFrame:
    """For each decision: EOF round before ('1 reunión adelante') vs after ('próxima RPM').

    Both refer to the same target meeting (the one after the decision)."""
    if tab.empty:
        return pd.DataFrame()
    nxt = mp_data.series("eof_tpm_next")
    one = mp_data.series("eof_tpm_rpm1")
    rows = []
    for d in tab["decision_date"]:
        pre = one.loc[
            pd.Timestamp(d - timedelta(days=window)) : pd.Timestamp(d - timedelta(days=1))
        ]
        post = nxt.loc[
            pd.Timestamp(d + timedelta(days=1)) : pd.Timestamp(d + timedelta(days=window))
        ]
        if len(pre) and len(post):
            rows.append(
                {
                    "decision": d,
                    "pre_round": pre.index[-1].date(),
                    "pre": pre.iloc[-1],
                    "post_round": post.index[0].date(),
                    "post": post.iloc[0],
                    "shift_bp": (post.iloc[0] - pre.iloc[-1]) * 100,
                }
            )
    return pd.DataFrame(rows)


def render() -> None:
    top, btn = st.columns([5, 1])
    with btn:
        refresh_row("mp_surveys", markets=False)
    start = date(date.today().year - 5, 1, 1)

    st.markdown("#### EEE (Encuesta de Expectativas Económicas) — TPM medians")
    st.plotly_chart(_lines(EEE_LINES, start, "EEE: TPM expectations by horizon"), width="stretch")
    series_caption([s for s, _, _ in EEE_LINES])
    eee = mp_data.wide(
        tuple(mp_data.EEE_IDS + ["eee_tpm_35m", "eee_tpm_lp", "eee_tpm_dec2026", "eee_tpm_dec2027"])
    ).dropna(how="all")
    if not eee.empty:
        last = eee.iloc[-1].dropna()
        st.markdown(f"Latest EEE ({eee.index[-1]:%B %Y}), medians:")
        st.dataframe(last.rename("median %").to_frame().T, hide_index=True, width="stretch")
    st.caption(
        "p10/p90 and respondent counts are not in the BDE; they come from the EEE PDFs "
        "(pending a decision on parsing them)."
    )

    st.markdown("#### EOF (Encuesta de Operadores Financieros) — TPM medians")
    st.plotly_chart(_lines(EOF_LINES, start, "EOF: TPM expectations by horizon"), width="stretch")
    series_caption([s for s, _, _ in EOF_LINES])

    st.markdown("#### How the EOF moved after each decision")
    tab = mp_data.decision_table(since=date(2018, 3, 1))
    sh = eof_shift_after_meetings(tab)
    if sh.empty:
        st.caption("No decision has EOF rounds on both sides within 25 days.")
    else:
        fig = go.Figure(
            go.Bar(
                x=sh["decision"],
                y=sh["shift_bp"],
                marker_color=ROLE["eof"],
                hovertemplate="%{x}: %{y:+.0f}bp",
            )
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(
            base_layout(
                fig, "EOF expectation for the following meeting: post − pre round (bp)", "bp", 280
            ),
            width="stretch",
        )
        with st.expander("Rounds used"):
            st.dataframe(sh.round(3), hide_index=True, width="stretch")
    st.caption(
        "Pre round: '1 reunión adelante' before the decision; post round: 'próxima RPM' "
        "after it. Both target the same next meeting. Decisions = rate changes until the "
        "full meeting calendar is configured."
    )

    st.markdown("#### Survey vs market")
    pending(
        "EEE/EOF vs SPC-implied expectation on each survey date needs the full meeting "
        "calendar (including holds) to know which meeting each survey targets."
    )
