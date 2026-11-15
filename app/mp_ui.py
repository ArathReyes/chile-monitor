"""Small UI helpers shared by the Monetary Policy sub-tabs."""

from __future__ import annotations

from datetime import datetime, time

import mp_data
import streamlit as st
from components import source_badge, stale_badge


def refresh_row(key: str, markets: bool = True, bde: bool = True, help_text: str = "") -> None:
    if st.button(
        "🔄 Refresh", key=f"refresh_{key}", help=help_text or "Re-fetch this sub-tab's data"
    ):
        with st.spinner("Fetching…"):
            res = mp_data.refresh(markets=markets, bde=bde)
        errors = [
            f"{k}: {v}"
            for grp in res.values()
            if isinstance(grp, dict)
            for k, v in grp.items()
            if isinstance(v, str) and v.startswith("error")
        ]
        if errors:
            st.warning("Some sources failed (last stored values shown):\n\n" + "\n".join(errors))
        else:
            st.toast("Data refreshed")


def series_caption(sids: list[str], source: str = "BCCh BDE") -> None:
    """Source + as-of caption, plus a stale badge for the first stale series (if any)."""
    source_badge(source, mp_data.fetched_at(tuple(sids)))
    for sid in sids:
        is_stale, last = mp_data.stale(sid)
        if is_stale:
            stale_badge(datetime.combine(last, time()) if last else None)
            st.caption(f"`{sid}` last observation: {last}")
            break


def pending(msg: str) -> None:
    st.info(msg, icon="⏳")
