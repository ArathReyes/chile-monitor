"""Chile Rates Monitor: Streamlit entry point. Run: uv run streamlit run app/main.py"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

st.set_page_config(page_title="Chile Rates Monitor", page_icon="🇨🇱", layout="wide")

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
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    now_scl = now_ny.astimezone(ZoneInfo("America/Santiago"))
    st.title("Chile Rates Monitor")
    st.caption(f"Public data only · {now_ny:%a %d %b %Y %H:%M} NY · {now_scl:%H:%M} Santiago")
    for tab, (_, module) in zip(st.tabs([t[0] for t in TABS]), TABS, strict=True):
        with tab:
            module.render()


main()
