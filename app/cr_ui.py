"""Shared UI helpers for the Curves & Rates sub-tabs."""

from __future__ import annotations

import cr_data
import streamlit as st
from components import source_badge
from mp_ui import pending  # noqa: F401  (re-exported)

from monitor.models import curves as C

CAPTION = (
    "Bond and swap data: BCCh BDE, end-of-day, transaction-based (T-1; tenors without trades are "
    "blank). Spread series are computed only where both legs traded on the same date."
)
WINDOWS = ["1W", "2W", "1M", "2M", "6M", "1Y", "2Y"]


def refresh_row(key: str) -> None:
    if st.button(
        "🔄 Refresh",
        key=f"refresh_{key}",
        help="Re-fetch BDE curve series, recompute derived spreads and NSS fits",
    ):
        with st.spinner("Fetching…"):
            res = cr_data.refresh()
        bde = res.get("bde", {})
        errs = (
            [f"{k}: {v}" for k, v in bde.items() if isinstance(v, str)]
            if isinstance(bde, dict)
            else []
        )
        errs += [f"{k}: {res[k]}" for k in ("derived", "nss_fits") if isinstance(res.get(k), str)]
        if errs:
            st.warning("Some steps failed (last stored values shown):\n\n" + "\n".join(errs[:10]))
        else:
            st.toast("Curves & Rates data refreshed")


def header(caption: str, key: str) -> None:
    c1, c2 = st.columns([5, 1])
    with c1:
        st.caption(caption)
    with c2:
        refresh_row(key)


def curve_caption(curves: list[str], extra: str = "") -> None:
    """Source + fetch time + the data's own as-of date per curve."""
    fetched = max((f for f in (cr_data.fetched(c) for c in curves) if f is not None), default=None)
    source_badge(
        "BCCh BDE" + (" (derived)" if any(c in C.SPREADS for c in curves) else ""), fetched
    )
    dates = [f"{C.CURVES[c].label}: {d:%d-%b-%Y}" for c in curves if (d := cr_data.ref_date(c))]
    if dates:
        st.caption("Data as of (obs_date) — " + " · ".join(dates) + (f". {extra}" if extra else ""))


def no_data(curve: str | None = None) -> bool:
    if curve is None or cr_data.panel(curve).empty:
        pending("Curve data not loaded. Press Refresh (runs `monitor.jobs.curves_daily`).")
        return True
    return False
