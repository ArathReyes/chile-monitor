"""Monetary Policy tab: five sub-tabs."""

from __future__ import annotations

import streamlit as st

from tabs import mp_board, mp_history, mp_implied, mp_overview, mp_surveys

SUBTABS = [
    ("Overview", mp_overview),
    ("Board & Communication", mp_board),
    ("Decision History", mp_history),
    ("Implied Path & Probabilities", mp_implied),
    ("Surveys", mp_surveys),
]


def render() -> None:
    for tab, (_, module) in zip(st.tabs([t[0] for t in SUBTABS]), SUBTABS, strict=True):
        with tab:
            module.render()
