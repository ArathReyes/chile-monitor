"""Shared UI helpers for the Chile Macro sub-tabs."""

from __future__ import annotations

import cm_data
import streamlit as st
from mp_ui import pending, series_caption  # noqa: F401  (re-exported)

TARGET_LO, TARGET_HI = 2.0, 4.0


def refresh_row(key: str) -> None:
    if st.button(
        "🔄 Refresh",
        key=f"refresh_{key}",
        help="Re-fetch BDE macro series, the INE calendar, and recompute surprises",
    ):
        with st.spinner("Fetching…"):
            res = cm_data.refresh()
        bde = res.get("bde", {})
        errs = (
            [f"{k}: {v}" for k, v in bde.items() if isinstance(v, str)]
            if isinstance(bde, dict)
            else []
        )
        for k in ("ine_calendar", "surprises"):
            if isinstance(res.get(k), str):
                errs.append(f"{k}: {res[k]}")
        if errs:
            st.warning("Some sources failed (last stored values shown):\n\n" + "\n".join(errs[:10]))
        else:
            st.toast("Chile Macro data refreshed")


def header(caption: str, key: str) -> None:
    c1, c2 = st.columns([5, 1])
    with c1:
        st.caption(caption)
    with c2:
        refresh_row(key)


def band(fig) -> None:
    fig.add_hrect(
        y0=TARGET_LO,
        y1=TARGET_HI,
        fillcolor="rgba(139,138,133,0.14)",
        line_width=0,
        annotation_text="target 3% ± 1",
        annotation_position="top left",
    )
