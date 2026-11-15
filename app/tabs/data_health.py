"""Data Health tab (implemented): ingest_log and series freshness."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st
from components import refresh_button, source_badge

from monitor import storage
from monitor.config import load_series
from monitor.health import is_stale


@st.cache_data(ttl=60)
def _load() -> dict[str, pd.DataFrame]:
    conn = storage.connect()
    try:
        return {
            "summary": storage.ingest_summary(conn),
            "errors": storage.recent_errors(conn),
            "series": storage.series_status(conn),
            "bonds": storage.read_bond_master(conn),
        }
    finally:
        conn.close()


def _registry_frame() -> pd.DataFrame:
    rows = [
        {
            "series_id": s.series_id,
            "source": s.source,
            "source_code": s.source_code,
            "freq": s.freq,
            "tab": s.tab,
            "verified": "✅" if s.verified else "⚠ VERIFY",
        }
        for s in load_series().series
    ]
    return pd.DataFrame(rows)


def render() -> None:
    head, btn = st.columns([5, 1])
    with head:
        st.subheader("Data Health")
        st.caption("Last fetch per source and series, staleness vs expected frequency, errors.")
    with btn:
        if refresh_button("data_health", disabled=False):
            _load.clear()

    data = _load()
    summary, errors, series = data["summary"], data["errors"], data["series"]

    st.markdown("#### Ingestion jobs")
    if summary.empty:
        st.info(
            "No ingestion runs yet. `ingest_log` fills when jobs run, either from a tab's "
            "Refresh button or with `uv run python -m monitor.jobs.<job>` once the first job "
            "spec is implemented. Check connectivity first with "
            "`uv run python scripts/check_keys.py`.",
            icon="📭",
        )
    else:
        st.dataframe(summary, hide_index=True, width="stretch")
        source_badge("ingest_log", summary["last_run"].max())

    st.markdown("#### Series freshness")
    if series.empty:
        st.info(
            "`series_meta` is empty. Ingestion jobs register series from `config/series.yaml` "
            "when they first run. The registry as currently configured is shown below.",
            icon="📭",
        )
    else:
        today = date.today()
        series["stale"] = [
            "⚠ stale" if is_stale(d.date() if hasattr(d, "date") else d, f, today) else "ok"
            for d, f in zip(
                series["last_obs"].where(series["last_obs"].notna(), None),
                series["freq"],
                strict=True,
            )
        ]
        st.dataframe(series, hide_index=True, width="stretch")

    st.markdown("#### Treasury bonds needing review (Bond Monitor)")
    bonds = data["bonds"]
    if bonds.empty:
        st.caption(
            "Bond master not loaded yet (`uv run python -m monitor.jobs.bond_master_weekly`)."
        )
    else:
        missing = bonds[bonds["source"] == "nemo"]
        odd = bonds[bonds["flagged"].fillna("").str.contains("exclude|differs")]
        st.caption(
            f"{len(missing)} bonds seen in Hacienda's calendar or results but missing from its "
            f"master files (decoded from the nemo, maturity day unknown); {len(odd)} with "
            "inconsistent coupon data in Hacienda's files."
        )
        review = pd.concat([missing, odd])[
            ["nemo", "instrument_type", "maturity_date", "source", "flagged"]
        ]
        st.dataframe(review, hide_index=True, width="stretch", height=220)

    st.markdown("#### Recent errors")
    if errors.empty:
        st.caption("No errors logged.")
    else:
        st.dataframe(errors, hide_index=True, width="stretch")

    st.markdown("#### Series registry (config/series.yaml)")
    st.dataframe(_registry_frame(), hide_index=True, width="stretch")
