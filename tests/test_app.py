"""Smoke tests: every tab and sub-tab renders without exceptions.

1. Empty database (empty states).
2. Synthetic fixture database (BDE-like series + a real Kalshi snapshot fixture).
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from monitor import storage
from monitor.sources.kalshi import parse_events

APP = Path(__file__).resolve().parents[1] / "app" / "main.py"
FIXTURES = Path(__file__).parent / "fixtures"
TAB_LABELS = [
    "Monetary Policy", "Chile Macro", "Curves & Rates", "FX & Flows", "Global Macro",
    "Calendar", "Bond Monitor", "Copper", "Thematic", "Data Health",
    "Overview", "Board & Communication", "Decision History",
    "Implied Path & Probabilities", "Surveys",
]  # fmt: skip


def _run(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.setattr("monitor.storage.DEFAULT_DB_PATH", tmp_path / "test.duckdb")
    st.cache_data.clear()  # cached reads would leak between parametrized runs
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    return at


def _fill(db: Path) -> None:
    conn = storage.connect(db)
    days = pd.bdate_range("2023-01-02", "2026-11-13")
    months = pd.date_range("2018-01-01", "2026-10-01", freq="MS")
    tpm = np.where(days < "2025-07-30", 4.75, 4.5)
    frames = {
        "cl_tpm": (days, tpm), "cl_tib": (days, tpm),
        "cl_spc_nom_90d": (days, tpm + 0.10), "cl_spc_nom_180d": (days, tpm + 0.18),
        "cl_spc_nom_360d": (days, tpm + 0.30), "cl_spc_nom_2y": (days, tpm + 0.6),
        "cl_cpi_yoy": (months, np.full(len(months), 3.5)),
        "cl_cpi_core_spliced": (months, 100 * 1.003 ** np.arange(len(months))),
        "cl_imacec": (months, 100 * 1.002 ** np.arange(len(months))),
    }  # fmt: skip
    eof_days = days[::15]
    for k in ["next", "rpm1", "rpm2_3m", "rpm3", "rpm4_6m", "12m", "24m"]:
        frames[f"eof_tpm_{k}"] = (eof_days, np.full(len(eof_days), 4.5))
    frames["eof_statement_bias"] = (eof_days, np.full(len(eof_days), 5.0))
    for k in ["this_month", "next_month", "5m", "11m", "17m", "23m", "35m", "lp"]:
        frames[f"eee_tpm_{k}"] = (months, np.full(len(months), 4.5))
    for sid, (idx, vals) in frames.items():
        df = pd.DataFrame({"series_id": sid, "obs_date": idx, "value": vals})
        storage.upsert_observations(conn, df, "fixture")
    ev = json.loads((FIXTURES / "kalshi_events_kxcbdchile.json").read_text())["events"]
    out = parse_events(ev)
    out["meeting_date"] = out["meeting_date"].astype(str)
    storage.insert_market_snapshot(
        conn, "kalshi", ev[0]["event_ticker"], {"outcomes": out.to_dict("records")}
    )
    conn.close()


@pytest.mark.parametrize("filled", [False, True], ids=["empty_db", "fixture_db"])
def test_all_tabs_render(tmp_path, monkeypatch, filled):
    if filled:
        _fill(tmp_path / "test.duckdb")
    at = _run(tmp_path, monkeypatch)
    assert not at.exception, at.exception
    labels = [t.label for t in at.tabs]
    for name in TAB_LABELS:
        assert name in labels, name
    if filled:
        assert any(m.label == "TPM" and m.value == "4.50%" for m in at.metric)
        assert len(at.get("plotly_chart")) >= 5
