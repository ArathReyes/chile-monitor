"""Data assembly for the Bond Monitor tab. Reads DuckDB; the only network calls are the Refresh
buttons (jobs layer) and hacienda.get_results' refresh-on-consult when a result is missing."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import mp_data
import pandas as pd
import streamlit as st

from monitor import storage
from monitor.cl_calendar import add_business_days, business_days, previous_business_day
from monitor.models import bonds as B
from monitor.sources import hacienda as h

TTL = 300
SCL = ZoneInfo("America/Santiago")
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
AUCTION_OPEN, AUCTION_CLOSE = (10, 30), (11, 0)  # Santiago, BCCh fiscal agency rules
# expected refresh cadence per job (hours) for the stale badge
JOB_MAX_AGE_H = {
    "hacienda.calendar": 30,
    "hacienda.results": 30,
    "hacienda.soma": 30,
    "hacienda.master": 24 * 8,
}


def _conn(read_only: bool = True):
    return storage.connect(read_only=read_only)


def refresh(kind: str = "daily") -> dict[str, object]:
    from monitor.jobs import auctions_daily, auctions_evening, bond_master_weekly

    job = {"daily": auctions_daily, "evening": auctions_evening, "master": bond_master_weekly}
    out = job[kind].run()
    st.cache_data.clear()
    return out


@st.cache_data(ttl=TTL)
def calendar() -> pd.DataFrame:
    c = _conn()
    try:
        return storage.read_auction_calendar(c)
    finally:
        c.close()


@st.cache_data(ttl=TTL)
def calendar_changes() -> pd.DataFrame:
    c = _conn()
    try:
        return storage.read_calendar_changes(c)
    finally:
        c.close()


@st.cache_data(ttl=TTL)
def results() -> pd.DataFrame:
    """Through hacienda.get_results: scrapes first if a past auction's result is missing."""
    c = _conn(read_only=False)
    try:
        return h.get_results(c)
    finally:
        c.close()


@st.cache_data(ttl=TTL)
def missing_dates() -> list[date]:
    c = _conn()
    try:
        return h.missing_result_dates(c)
    finally:
        c.close()


@st.cache_data(ttl=TTL)
def master() -> pd.DataFrame:
    c = _conn()
    try:
        return storage.read_bond_master(c)
    finally:
        c.close()


@st.cache_data(ttl=TTL)
def outstanding() -> pd.DataFrame:
    c = _conn()
    try:
        return storage.read_bond_outstanding(c)
    finally:
        c.close()


@st.cache_data(ttl=TTL)
def job_status() -> pd.DataFrame:
    c = _conn()
    try:
        s = storage.ingest_summary(c)
        return s[s["job"].str.startswith("hacienda.")]
    finally:
        c.close()


def last_success(job: str) -> datetime | None:
    s = job_status()
    row = s[s["job"] == job]
    if row.empty or pd.isna(row["last_success"].iloc[0]):
        return None
    return row["last_success"].iloc[0].to_pydatetime()


def is_stale(job: str) -> bool:
    last = last_success(job)
    if last is None:
        return True
    return datetime.now(UTC).replace(tzinfo=None) - last > timedelta(
        hours=JOB_MAX_AGE_H.get(job, 30)
    )


def uf() -> pd.Series:
    return mp_data.series("cl_uf")


def usdclp() -> pd.Series:
    return mp_data.series("cl_usdclp")


def with_usd(df: pd.DataFrame, amount_col: str, date_col: str) -> pd.DataFrame:
    if df.empty:
        return df.assign(**{f"{amount_col}_clp": [], f"{amount_col}_usd": [], "fx_date": []})
    return B.add_nominal_amounts(df, amount_col, uf(), usdclp(), date_col)


def auction_window(d: date) -> tuple[datetime, datetime]:
    """Bidding window 10:30-11:00 Santiago on date d (tz-aware)."""
    o = datetime(d.year, d.month, d.day, *AUCTION_OPEN, tzinfo=SCL)
    c = datetime(d.year, d.month, d.day, *AUCTION_CLOSE, tzinfo=SCL)
    return o, c


def soma() -> pd.DataFrame:
    """Authorized and placed (USD mm) by scrape date for the current year (vintages)."""
    y = date.today().year
    a = mp_data.series(f"hacienda_auth_usd_{y}")
    p = mp_data.series(f"hacienda_placed_usd_{y}")
    return pd.DataFrame({"authorized": a, "placed": p})


# --------------------------------------------------------- curve proxy (concession, event study)

NOM_SPREAD = {
    2.0: ("cl_bcp_2y", "cl_spc_nom_2y"),
    5.0: ("cl_bcp_5y", "cl_spc_nom_5y"),
    10.0: ("cl_bcp_10y", "cl_spc_nom_10y"),
}
REAL_SPREAD = {
    2.0: ("cl_bcu_2y", "cl_spc_uf_2y"),
    5.0: ("cl_bcu_5y", "cl_spc_uf_5y"),
    10.0: ("cl_bcu_10y", "cl_spc_uf_10y"),
    20.0: ("cl_bcu_20y", "cl_spc_uf_20y"),
}
SPREAD_MAX_AGE_D = 14  # calendar days an observed bond−swap spread stays usable (as-of)


@st.cache_resource(ttl=TTL)
def _fits(curve: str) -> pd.Series:
    c = _conn()
    try:
        f = storage.read_curve_fits(c, curve)
    finally:
        c.close()
    f = f[f["params_json"].notna()]
    return pd.Series(
        [json.loads(p) for p in f["params_json"]], index=pd.DatetimeIndex(f["obs_date"])
    )


@st.cache_resource(ttl=TTL)
def _spreads(kind: str) -> pd.DataFrame:
    """Observed bond − swap (pp) by tenor, only on dates both legs traded."""
    pairs = NOM_SPREAD if kind == "nominal" else REAL_SPREAD
    ids = tuple(x for p in pairs.values() for x in p)
    w = mp_data.wide(ids)
    return pd.DataFrame({t: (w[b] - w[s]) for t, (b, s) in pairs.items()}).dropna(how="all")


def curve_day(kind: str, d: date) -> B.CurveDay:
    """As-of inputs on date d: last NSS fit on/before d; last observed spread per tenor within
    SPREAD_MAX_AGE_D days."""
    return _curve_day(kind, d, id(_fits("nom_swap")), id(_spreads(kind)))


@lru_cache(maxsize=20_000)
def _curve_day(kind: str, d: date, _fits_id: int, _sp_id: int) -> B.CurveDay:
    fits = _fits("nom_swap" if kind == "nominal" else "real_swap")
    ts = pd.Timestamp(d)
    f = fits.loc[:ts]
    params = f.iloc[-1] if len(f) else None
    sp = _spreads(kind).loc[ts - pd.Timedelta(days=SPREAD_MAX_AGE_D) : ts]
    spreads = {float(t): float(sp[t].dropna().iloc[-1]) for t in sp.columns if sp[t].notna().any()}
    return B.CurveDay(params, spreads)


# Concession / event study only inside the benchmark range (no extrapolation): nominal bonds
# 2-10Y (BCP + SPC to 10Y), real 2-20Y (BCU + SPC UF to 20Y). Letras (< 2Y) have no short-end
# bond benchmark since the 1Y stopped trading in Dec 2025.
PROXY_RANGE = {"nominal": (2.0, 10.0), "real": (2.0, 20.0)}
TENOR_TOL = 0.5
SUSPECT_BP = 200.0


def maturity_of(nemo: str, fallback) -> date | None:
    m = master()
    row = m[m["nemo"] == nemo]
    if len(row) and pd.notna(row["maturity_date"].iloc[0]):
        return row["maturity_date"].iloc[0].date()
    return pd.Timestamp(fallback).date() if pd.notna(fallback) else None


def curve_yield_at(nemo: str, itype: str, maturity, on: date) -> float | None:
    """Curve-proxy yield at the bond's maturity on date `on`; None outside the benchmark range."""
    mat = maturity_of(nemo, maturity)
    if mat is None or mat <= on:
        return None
    kind = "real" if itype == "BTU" else "nominal"
    t = B.years_between(on, mat)
    lo, hi = PROXY_RANGE[kind]
    if not lo - TENOR_TOL <= t <= hi + TENOR_TOL:
        return None
    return B.curve_yield(t, curve_day(kind, on))


@st.cache_data(ttl=TTL)
def results_enriched() -> pd.DataFrame:
    """Results + bid-to-cover, allocation shares, curve yield on T−1 and concession (bp)."""
    r = results()
    if r.empty:
        return r
    r = r.copy()
    shares = [
        B.allocation_shares(a, b, o)
        for a, b, o in zip(r["allocated"], r["banks"], r["afp_ins_brokers"], strict=True)
    ]
    r["pct_banks"] = [s[0] for s in shares]
    r["pct_afp_ins"] = [s[1] for s in shares]
    t1 = [previous_business_day(d.date()) for d in r["auction_date"]]
    r["curve_t1"] = [
        curve_yield_at(n, t, m, d)
        for n, t, m, d in zip(r["nemo"], r["instrument_type"], r["maturity"], t1, strict=True)
    ]
    conc = [B.concession_bp(a, b) for a, b in zip(r["rate"], r["curve_t1"], strict=True)]
    # |concession| > 200bp means the rate doesn't belong to that curve (e.g. 2022 rows labelled
    # BTP0470930 clearing at ~2%, a real yield): kept, flagged, excluded from statistics.
    r["suspect"] = [c is not None and abs(c) > SUSPECT_BP for c in conc]
    r["concession_bp"] = [None if bad else c for c, bad in zip(conc, r["suspect"], strict=True)]
    r["allocated_clp"] = [
        B.to_clp(a, u, None if u != "M UF" else _uf_on(d))
        for a, u, d in zip(r["allocated"], r["unit"], r["auction_date"], strict=True)
    ]
    r["allocated_usd"] = [
        B.to_usd(c, B.asof_value(usdclp(), d.date())[0])
        for c, d in zip(r["allocated_clp"], r["auction_date"], strict=True)
    ]
    return r


def _uf_on(d) -> float | None:
    return B.asof_value(uf(), pd.Timestamp(d).date())[0]


EVENT_OFFSETS = list(range(-5, 6))


@st.cache_data(ttl=TTL)
def event_study() -> pd.DataFrame:
    """Per auction (ok status): curve-proxy yield path at the bond's maturity, T−5…T+5 business
    days, in bp vs T−1. Offsets beyond the latest curve date are left blank."""
    r = results_enriched()
    if r.empty:
        return pd.DataFrame()
    last_fit = min(
        (_fits(c).index.max() for c in ("nom_swap", "real_swap") if len(_fits(c))), default=None
    )
    rows = []
    for x in r[r["status"] == "ok"].itertuples():
        d0 = x.auction_date.date()
        ys = {}
        for k in EVENT_OFFSETS:
            dk = add_business_days(d0, k) if k >= 0 else _back(d0, -k)
            ys[k] = (
                curve_yield_at(x.nemo, x.instrument_type, x.maturity, dk)
                if last_fit is not None and pd.Timestamp(dk) <= last_fit
                else None
            )
        path = B.event_path(ys)
        rows.append(
            {
                "auction_date": x.auction_date,
                "nemo": x.nemo,
                "segment": "Real (UF)" if x.instrument_type == "BTU" else "Nominal (CLP)",
                "bid_to_cover": x.bid_to_cover,
                "concession_bp": x.concession_bp,
                "allocated_usd": x.allocated_usd,
                **{f"d{k}": v for k, v in path.items()},
            }
        )
    out = pd.DataFrame(rows)
    out["btc_tercile"] = B.btc_terciles(out["bid_to_cover"])
    return out


def _back(d: date, n: int) -> date:
    for _ in range(n):
        d = previous_business_day(d)
    return d


def business_days_until(d: date) -> int:
    return len(business_days(date.today(), d))
