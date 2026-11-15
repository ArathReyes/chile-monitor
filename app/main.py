"""Chile Monitor: Streamlit entry point. Run: uv run streamlit run app/main.py"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Chile Monitor", page_icon="🇨🇱", layout="wide")

import bootstrap  # noqa: E402
import style  # noqa: E402
from tabs import (  # noqa: E402  (set_page_config must run first)
    bond_monitor,
    calendar_tab,
    chile_macro,
    copper,
    curves_rates,
    data_health,
    fx_flows,
    global_macro,
    monetary_policy,
    thematic,
)

TABS = [
    ("Monetary Policy", monetary_policy),
    ("Chile Macro", chile_macro),
    ("Curves & Rates", curves_rates),
    ("FX & Flows", fx_flows),
    ("Global Macro", global_macro),
    ("Calendar", calendar_tab),
    ("Bond Monitor", bond_monitor),
    ("Copper", copper),
    ("Thematic", thematic),
    ("Data Health", data_health),
]


def main() -> None:
    bootstrap.ensure_db()
    style.apply()
    style.header()
    if at := bootstrap.published_at():
        st.caption(f"Data published by the morning job {at}; Refresh buttons fetch newer data.")
    for tab, (_, module) in zip(st.tabs([t[0] for t in TABS]), TABS, strict=True):
        with tab:
            module.render()


main()
