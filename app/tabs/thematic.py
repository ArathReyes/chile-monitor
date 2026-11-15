"""Thematic tab (placeholder): lists the configured theme cards."""

import streamlit as st
from components import SourceRow, TabSpec, placeholder_card

from monitor.config import load_themes

SPEC = TabSpec(
    "Thematic",
    "Config-driven theme cards for live macro narratives (config/themes/*.yaml).",
    [
        "One card per theme: thesis, key indicators, open questions",
        "Indicator charts from yfinance / FRED / EIA",
        "Claude-written summaries (optional, needs ANTHROPIC_API_KEY)",
    ],
    [
        SourceRow("Market prices", "yfinance", "Python lib (unofficial)", "none", "Daily"),
        SourceRow("Macro series", "FRED", "REST", "FRED_API_KEY", "Daily / monthly"),
        SourceRow("Oil inventories", "EIA", "REST", "EIA_API_KEY (opt.)", "Weekly"),
        SourceRow(
            "Summaries", "Anthropic API", "anthropic SDK", "ANTHROPIC_API_KEY (opt.)", "On demand"
        ),
    ],
)


def render() -> None:
    placeholder_card(SPEC, key="thematic")
    st.markdown("**Configured themes**")
    for theme in load_themes():
        with st.expander(theme.title):
            st.write(theme.thesis)
            st.markdown("\n".join(f"- `{i.source}:{i.code}`: {i.label}" for i in theme.indicators))
