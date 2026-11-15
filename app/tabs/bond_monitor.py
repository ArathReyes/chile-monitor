"""Bond Monitor tab: five sub-tabs (spec 04)."""

from __future__ import annotations

import streamlit as st

from tabs import bm_impact, bm_master, bm_results, bm_supply, bm_upcoming

SUBTABS = [
    ("Upcoming Auctions", bm_upcoming),
    ("Auction Results", bm_results),
    ("Supply & Issuance", bm_supply),
    ("Bond Master", bm_master),
    ("Auction Impact", bm_impact),
]


def render() -> None:
    for tab, (_, module) in zip(st.tabs([t[0] for t in SUBTABS]), SUBTABS, strict=True):
        with tab:
            module.render()
