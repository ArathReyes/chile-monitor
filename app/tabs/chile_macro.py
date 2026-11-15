"""Chile Macro tab: seven sub-tabs."""

from __future__ import annotations

import streamlit as st

from tabs import (
    cm_activity,
    cm_inflation,
    cm_labor,
    cm_overview,
    cm_seasonality,
    cm_surprises,
    cm_uf,
)

SUBTABS = [
    ("Overview", cm_overview),
    ("Inflation", cm_inflation),
    ("Seasonality & Nowcast", cm_seasonality),
    ("UF & Carry", cm_uf),
    ("Activity", cm_activity),
    ("Labor & External", cm_labor),
    ("Surprises & Calendar", cm_surprises),
]


def render() -> None:
    for tab, (_, module) in zip(st.tabs([t[0] for t in SUBTABS]), SUBTABS, strict=True):
        with tab:
            module.render()
