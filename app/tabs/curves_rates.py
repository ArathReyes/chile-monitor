"""Curves & Rates tab: six sub-tabs (spec 03)."""

from __future__ import annotations

import streamlit as st

from tabs import cr_breakevens, cr_carry, cr_quality, cr_shape, cr_snapshot, cr_world

SUBTABS = [
    ("Curve Snapshot", cr_snapshot),
    ("Spreads & Shape", cr_shape),
    ("Breakevens & Real Rates", cr_breakevens),
    ("Carry & Rolldown", cr_carry),
    ("Chile vs World", cr_world),
    ("Data Quality", cr_quality),
]


def render() -> None:
    for tab, (_, module) in zip(st.tabs([t[0] for t in SUBTABS]), SUBTABS, strict=True):
        with tab:
            module.render()
