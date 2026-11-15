"""Sub-tab: Board & Communication (mostly pending manual documents)."""

from __future__ import annotations

from datetime import date

import mp_data
import plotly.graph_objects as go
import streamlit as st
from charts import ROLE, base_layout, line
from mp_ui import pending, series_caption


def render() -> None:
    st.markdown("#### Board")
    pending(
        "Board cards (name, role, term, dissent count) need `config/board.yaml` or the saved "
        "Consejo page in `data/manual/bcch_web/board/`."
    )
    st.markdown("#### Statements and minutes")
    pending(
        "Statement archive, diff vs previous statement, minutes and the vote record need the "
        "saved RPM pages or PDFs in `data/manual/bcch_web/statements/` and `.../minutes/`. "
        "The diff engine is ready."
    )
    st.markdown("#### Market-perceived statement bias (EOF)")
    s = mp_data.series("eof_statement_bias", date(2021, 1, 1))
    if s.empty:
        st.caption("No data yet.")
        return
    fig = go.Figure()
    line(fig, s.index, s.values, "EOF: perceived bias of BCCh statements", ROLE["eof"], shape="hv")
    fig.update_traces(mode="lines+markers")
    st.plotly_chart(
        base_layout(fig, "EOF: 'Percepción del sesgo Comunicados BCCh'", "index", 300),
        width="stretch",
    )
    series_caption(["eof_statement_bias"])
    st.warning(
        "Scale and direction of this EOF series are not confirmed (values range "
        f"{s.min():g}–{s.max():g}). Don't read it as hawk/dove until the EOF "
        "methodology is checked.",
        icon="⚠️",
    )
