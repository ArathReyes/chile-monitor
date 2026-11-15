"""Data assembly for the Curves & Rates tab (reads DuckDB + runs models; no API calls except the
Refresh action, which runs jobs.curves_daily)."""

from __future__ import annotations

import json
from datetime import date

import mp_data
import pandas as pd
import streamlit as st

from monitor import storage
from monitor.config import load_series
from monitor.models import curves as C

TTL = 600
TAB = "curves_rates"


def refresh() -> dict[str, object]:
    from monitor.jobs import curves_daily

    out = curves_daily.run()
    st.cache_data.clear()
    return out


@st.cache_data(ttl=TTL)
def _specs() -> list[dict]:
    return [s.model_dump() for s in load_series().series if s.tab == TAB and s.curve]


def points(curve: str) -> list[C.CurvePoint]:
    return C.points_from_specs([_Spec(**s) for s in _specs()], curve)


class _Spec:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def spread_ids(curve: str) -> list[tuple[str, float]]:
    return sorted(
        ((s["series_id"], s["tenor_years"]) for s in _specs() if s["curve"] == curve),
        key=lambda x: x[1],
    )


def series_ids(curve: str) -> list[str]:
    if curve in C.OUTRIGHT:
        return [p.series_id for p in points(curve)]
    return [sid for sid, _ in spread_ids(curve)]


@st.cache_data(ttl=TTL)
def panel(curve: str) -> pd.DataFrame:
    """obs_date x tenor: % on a 365 basis (outright) or bp (stored derived spreads)."""
    if curve in C.OUTRIGHT:
        pts = points(curve)
        return C.curve_panel(mp_data.wide(tuple(p.series_id for p in pts)), pts)
    ids = spread_ids(curve)
    w = mp_data.wide(tuple(sid for sid, _ in ids))
    out = w.rename(columns={sid: float(t) for sid, t in ids})
    out.columns.name = "tenor"
    return out.dropna(how="all")


def ref_date(curve: str) -> date | None:
    return C.latest_date(panel(curve))


def fetched(curve: str):
    return mp_data.fetched_at(tuple(series_ids(curve)))


@st.cache_data(ttl=TTL)
def fits(curve: str) -> pd.DataFrame:
    conn = storage.connect()
    try:
        return storage.read_curve_fits(conn, curve)
    finally:
        conn.close()


def fit_params(curve: str, d: date | None = None) -> tuple[dict | None, date | None]:
    """Latest successful NSS params on or before d."""
    f = fits(curve)
    if f.empty:
        return None, None
    f = f[f["params_json"].notna()]
    if d is not None:
        f = f[f["obs_date"] <= pd.Timestamp(d)]
    if f.empty:
        return None, None
    r = f.iloc[-1]
    return json.loads(r["params_json"]), r["obs_date"].date()


@st.cache_data(ttl=TTL)
def fit_param_history(curve: str) -> dict[pd.Timestamp, dict]:
    f = fits(curve)
    f = f[f["params_json"].notna()] if not f.empty else f
    return {r.obs_date: json.loads(r.params_json) for r in f.itertuples()}


@st.cache_data(ttl=TTL)
def nss_forward_history(curve: str, a: float, b: float) -> pd.Series:
    hist = fit_param_history(curve)
    return pd.Series({d: C.nss_forward(p, a, b) for d, p in hist.items()}, dtype=float)


@st.cache_data(ttl=TTL)
def tpm_path() -> dict:
    """Implied TPM path (Monetary Policy model) for funding; falls back to the current TPM."""
    try:
        fit, fi, _ = mp_data.implied_fit()
        steps = [(r.effective_date, float(r.implied_tpm)) for r in fit.path.itertuples()]
        return {
            "tpm0": fi.tpm0,
            "steps": steps,
            "spread_bp": fi.spread_bp,
            "as_of": fi.curve_date,
            "fallback": None,
        }
    except Exception as e:  # noqa: BLE001  (no swaps / TPM loaded)
        tpm = mp_data.series("cl_tpm")
        if tpm.empty:
            return {"tpm0": None, "steps": [], "spread_bp": 0.0, "as_of": None, "fallback": str(e)}
        why = str(e) or "implied path unavailable"
        return {
            "tpm0": float(tpm.iloc[-1]),
            "steps": [],
            "spread_bp": 0.0,
            "as_of": tpm.index[-1].date(),
            "fallback": why,
        }


def funding(start: date, h: str) -> float | None:
    p = tpm_path()
    if p["tpm0"] is None:
        return None
    end = start + pd.DateOffset(months={"1M": 1, "3M": 3}[h])
    return C.avg_path_rate(p["tpm0"], p["steps"], start, end.date(), p["spread_bp"])


def today_curve(curve: str) -> pd.DataFrame:
    ref = ref_date(curve)
    return C.curve_asof(panel(curve), ref) if ref else pd.DataFrame()


@st.cache_data(ttl=TTL)
def placeholders() -> pd.DataFrame:
    """Zero placeholder prints dropped from the curve series (shown in Data Quality)."""
    rows = []
    for curve in C.OUTRIGHT:
        for p in points(curve):
            s = mp_data.series(p.series_id)
            prev, nxt = s.shift(1), s.shift(-1)
            for d in C.zero_placeholders(s):
                rows.append(
                    {
                        "series": p.series_id,
                        "curve": C.CURVES[curve].label,
                        "tenor": C.tenor_label(p.tenor),
                        "obs_date": d.date(),
                        "value": 0.0,
                        "previous": prev[d],
                        "next": nxt[d],
                    }
                )
    return pd.DataFrame(rows)
