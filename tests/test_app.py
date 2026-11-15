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
    "Monetary Policy",
    "Chile Macro",
    "Curves & Rates",
    "FX & Flows",
    "Global Macro",
    "Calendar",
    "Bond Monitor",
    "Copper",
    "Thematic",
    "Data Health",
    "Overview",
    "Board & Communication",
    "Decision History",
    "Implied Path & Probabilities",
    "Surveys",
    "Inflation",
    "Seasonality & Nowcast",
    "UF & Carry",
    "Activity",
    "Labor & External",
    "Surprises & Calendar",
    "Curve Snapshot",
    "Spreads & Shape",
    "Breakevens & Real Rates",
    "Carry & Rolldown",
    "Chile vs World",
    "Data Quality",
    "Upcoming Auctions",
    "Auction Results",
    "Supply & Issuance",
    "Bond Master",
    "Auction Impact",
]


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
        "cl_tpm": (days, tpm),
        "cl_tib": (days, tpm),
        "cl_spc_nom_90d": (days, tpm + 0.10),
        "cl_spc_nom_180d": (days, tpm + 0.18),
        "cl_spc_nom_360d": (days, tpm + 0.30),
        "cl_spc_nom_2y": (days, tpm + 0.6),
        "cl_cpi_yoy": (months, np.full(len(months), 3.5)),
        "cl_cpi_core_spliced": (months, 100 * 1.003 ** np.arange(len(months))),
        "cl_imacec": (months, 100 * 1.002 ** np.arange(len(months))),
    }
    long_months = pd.date_range("2012-01-01", "2026-10-01", freq="MS")
    n = len(long_months)
    seasonal = np.tile([0.8, 0.2, 0.5, 0.4, 0.3, 0.1, 0.4, 0.3, 0.6, 0.5, 0.2, 0.1], n // 12 + 1)[
        :n
    ]
    idx_level = 100 * np.cumprod(1 + seasonal / 100)
    for sid in [
        "cl_cpi_mm_hist",
        "cl_cpi_core_mm",
        "eee_cpi_mm_this_month",
        "eee_cpi_mm_next_month",
    ]:
        frames[sid] = (long_months, seasonal)
    for sid in [
        "cl_cpi_spliced",
        "cl_cpi_core_spliced",
        "cl_cpi_sv",
        "cl_cpi_goods",
        "cl_cpi_services",
        "cl_cpi_tradables",
        "cl_cpi_nontradables",
        "cl_imacec",
        "cl_imacec_sa",
        "cl_imacec_mining",
        "cl_imacec_nonmining",
        "cl_imacec_pb",
        "cl_imacec_com",
        "cl_imacec_serv",
        "cl_employment",
        "cl_wages_nominal",
        "cl_wages_real",
        "cl_icmo",
    ]:
        frames[sid] = (long_months, idx_level)
    frames["cl_cpi_yoy"] = (long_months, np.full(n, 3.5))
    for sid in [
        "cl_unemp",
        "cl_unemp_sa",
        "eee_imacec_prev_month",
        "eee_imacec_nm_prev_month",
        "eee_cpi_yoy_11m",
        "eee_cpi_yoy_23m",
    ]:
        frames[sid] = (long_months, np.full(n, 3.0))
    for sid in ["cl_exports", "cl_imports", "cl_trade_balance", "cl_exports_copper"]:
        frames[sid] = (long_months, np.full(n, 1000.0))
    q = pd.date_range("2012-01-01", "2026-04-01", freq="QS")
    frames["cl_gdp"] = (q, 100 * 1.005 ** np.arange(len(q)))
    frames["cl_gdp_sa"] = frames["cl_gdp"]
    frames["cl_ca_gdp"] = (q, np.full(len(q), -3.0))
    uf_days = pd.date_range("2026-01-09", "2026-12-09", freq="D")
    frames["cl_uf"] = (uf_days, 40000 * 1.0001 ** np.arange(len(uf_days)))
    for sid, v in [
        ("cl_usdclp", 950.0),
        ("cu_lme", 4.5),
        ("cl_bcu_2y", 2.0),
        ("cl_bcp_2y", 5.0),
        ("cl_bei_swap_1y", 3.2),
        ("cl_bei_swap_2y", 3.1),
        ("cl_bei_swap_3y2y", 3.0),
        ("cl_bei_bond_2y", 3.1),
    ]:
        frames[sid] = (days, np.full(len(days), v))
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
    from monitor.jobs.macro_daily import compute_surprises

    storage.replace_release_surprises(conn, compute_surprises(conn))
    _fill_curves(conn)
    _fill_auctions(conn)
    conn.close()


def _fill_auctions(conn) -> None:
    """Hacienda tables from the saved real pages and files (no network)."""
    from datetime import date

    from monitor.sources import hacienda as h

    fx = FIXTURES / "hacienda"
    h.store_calendar(conn, h.parse_calendar((fx / "calendar_2026-11-15.html").read_text()))
    h.store_results(conn, h.parse_results((fx / "results_2026-11-15.html").read_text()), "live")
    rows = h.load_consolidated((fx / "licitaciones_2026.xlsx").read_bytes(), 2026)
    storage.upsert_auction_results(conn, [r.record() for r in rows], "consolidated")
    stock, out = h.load_stock((fx / "stock_total_2026-11-15.xlsx").read_bytes(), date(2026, 11, 15))
    chars = h.parse_characteristics((fx / "caracteristicas_2026-11-15.html").read_text())
    storage.replace_bond_master(conn, h.build_master(stock, chars, []))
    storage.upsert_bond_outstanding(conn, out)
    soma = pd.DataFrame(
        {
            "series_id": ["hacienda_auth_usd_2026", "hacienda_placed_usd_2026"],
            "obs_date": [date(2026, 11, 15)] * 2,
            "value": [23600.0, 21563.0],
        }
    )
    storage.upsert_observations(conn, soma, "fixture")


def _fill_curves(conn) -> None:
    """Curve points from the registry (shape + noise), then derived spreads and NSS fits."""
    from monitor.config import load_series
    from monitor.jobs.curves_daily import compute_derived, compute_fits

    rng = np.random.default_rng(3)
    cdays = pd.bdate_range("2026-01-02", "2026-11-13")
    for s in load_series().series:
        if (
            not s.curve
            or s.derived
            or s.series_id
            in ("cl_spc_nom_90d", "cl_spc_nom_180d", "cl_spc_nom_360d", "cl_bcu_2y", "cl_bcp_2y")
        ):
            continue
        level = (2.0 if s.curve.startswith("real") else 4.8) + 0.08 * np.log1p(s.tenor_years)
        vals = level + np.cumsum(rng.normal(0, 0.02, len(cdays)))
        keep = rng.random(len(cdays)) > (0.3 if s.curve.endswith("bond") else 0.02)
        df = pd.DataFrame({"series_id": s.series_id, "obs_date": cdays[keep], "value": vals[keep]})
        storage.upsert_observations(conn, df, "fixture")
    compute_derived(conn)
    compute_fits(conn)


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
