"""Shared UI helpers for the Bond Monitor sub-tabs."""

from __future__ import annotations

from datetime import datetime

import bm_data
import pandas as pd
import streamlit as st
from components import source_badge, stale_badge
from cr_ui import text_table  # noqa: F401  (re-exported)
from mp_ui import pending  # noqa: F401  (re-exported)

HACIENDA = "Hacienda ODP"
FMT_INT = "{:,.0f}"


def refresh_row(key: str, kind: str, label: str) -> None:
    if st.button("🔄 Refresh", key=f"refresh_{key}", help=label):
        with st.spinner("Fetching from Hacienda…"):
            res = bm_data.refresh(kind)
        errs = [f"{k}: {v}" for k, v in res.items() if isinstance(v, str) and v.startswith("error")]
        if errs:
            st.warning("Some steps failed (last stored data shown):\n\n" + "\n".join(errs))
        else:
            st.toast("Bond Monitor data refreshed")


def header(caption: str, key: str, kind: str = "daily", label: str = "") -> None:
    c1, c2 = st.columns([5, 1])
    with c1:
        st.caption(caption)
    with c2:
        refresh_row(key, kind, label or "Calendar, latest result and SOMA status")


def job_caption(jobs: list[str], source: str = HACIENDA) -> None:
    """Source + last successful scrape; stale badge if older than the job's cadence."""
    lasts = [t for j in jobs if (t := bm_data.last_success(j)) is not None]
    source_badge(source, max(lasts) if lasts else None)
    stale = [j for j in jobs if bm_data.is_stale(j)]
    if stale:
        last = bm_data.last_success(stale[0])
        stale_badge(last)
        st.caption(f"Last successful `{stale[0]}`: {last or 'never'} UTC")


def money(x: float | None, unit: str = "") -> str:
    if x is None or pd.isna(x):
        return "–"
    for div, suf in ((1e12, "tn"), (1e9, "bn"), (1e6, "mm")):
        if abs(x) >= div:
            return f"{unit}{x / div:,.2f}{suf}"
    return f"{unit}{x:,.0f}"


def countdown(target: datetime, now: datetime) -> str:
    s = (target - now).total_seconds()
    if s <= 0:
        return "done"
    d, rem = divmod(int(s), 86400)
    hh = rem // 3600
    return f"{d}d {hh}h" if d else f"{hh}h {rem % 3600 // 60}m"
