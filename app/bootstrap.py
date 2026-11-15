"""Deployed app: fetch the database the morning job published (GitHub Release asset).

Active only when AUTO_PULL_DB=1 (set in the Streamlit Cloud secrets), so a local run never
overwrites data/monitor.duckdb. Checks for a newer file at most every CHECK_S seconds.
"""

from __future__ import annotations

import time
from zoneinfo import ZoneInfo

import streamlit as st

from monitor import release, storage
from monitor.config import get_secret

CHECK_S = 900
NY = ZoneInfo("America/New_York")


@st.cache_resource
def _state() -> dict:
    return {"checked": 0.0, "remote": None, "error": None}


def ensure_db() -> None:
    if get_secret("AUTO_PULL_DB") != "1":
        return
    s = _state()
    have = storage.DEFAULT_DB_PATH.exists()
    if have and time.time() - s["checked"] < CHECK_S:
        return
    s["checked"] = time.time()
    try:
        info = release.remote_info()
        if info is not None and (not have or info.updated_at != s["remote"]):
            with st.spinner("Loading the latest data…"):
                release.pull()
            s["remote"] = info.updated_at
            st.cache_data.clear()
        s["error"] = None
    except Exception as e:  # noqa: BLE001  (keep serving the copy we have)
        s["error"] = f"{type(e).__name__}: {e}"
    if s["error"]:
        st.warning(f"Could not check for newer data ({s['error']}); showing the last copy.")


def published_at() -> str | None:
    r = _state()["remote"]
    return None if r is None else r.astimezone(NY).strftime("%d-%b %H:%M NY")
