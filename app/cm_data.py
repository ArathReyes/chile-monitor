"""Data assembly for the Chile Macro tab (reads DuckDB + runs models; no API calls except the
Refresh action, which runs the jobs layer)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import mp_data
import pandas as pd
import streamlit as st
import yaml

from monitor import storage
from monitor.config import CONFIG_DIR
from monitor.models.seasonality import seasonal_stats
from monitor.models.transforms import yoy

TTL = 600
SCL = ZoneInfo("America/Santiago")
NY = ZoneInfo("America/New_York")
series = mp_data.series
wide = mp_data.wide
fetched_at = mp_data.fetched_at

INFLATION_IDS = [
    "cl_cpi_mm_hist",
    "cl_cpi_yoy",
    "cl_cpi_spliced",
    "cl_cpi_core_spliced",
    "cl_cpi_core_mm",
    "cl_cpi_sv",
    "cl_cpi_goods",
    "cl_cpi_services",
    "cl_cpi_tradables",
    "cl_cpi_nontradables",
    "cl_cpi_volatile",
]


def refresh() -> dict[str, object]:
    from monitor.jobs import macro_daily

    out = macro_daily.run()
    st.cache_data.clear()
    return out


@st.cache_data(ttl=TTL)
def releases() -> pd.DataFrame:
    conn = storage.connect()
    try:
        return storage.read_releases(conn)
    finally:
        conn.close()


@st.cache_data(ttl=TTL)
def surprises() -> pd.DataFrame:
    conn = storage.connect()
    try:
        return storage.read_release_surprises(conn)
    finally:
        conn.close()


def next_release(indicator: str) -> datetime | None:
    r = releases()
    if r.empty:
        return None
    fut = r[
        (r["indicator"] == indicator)
        & (r["release_local"] >= pd.Timestamp.now(tz=SCL).tz_localize(None))
    ]
    return None if fut.empty else fut["release_local"].min().to_pydatetime()


def fmt_release(dt: datetime | None) -> str:
    if dt is None:
        return "n/a"
    loc = dt.replace(tzinfo=SCL)
    return f"{loc:%a %d %b %H:%M} Santiago ({loc.astimezone(NY):%H:%M} NY)"


@st.cache_data(ttl=TTL)
def cpi_weights() -> dict[str, dict]:
    p = CONFIG_DIR / "cpi_weights.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


@st.cache_data(ttl=TTL)
def seasonal(
    window: int = 10, exclude: tuple[int, ...] = (2021, 2022), sid: str = "cl_cpi_mm_hist"
):
    return seasonal_stats(series(sid), window_years=window, exclude_years=exclude)


def yoy_of(sid: str) -> pd.Series:
    return yoy(series(sid)).dropna()


def latest(s: pd.Series) -> tuple[float | None, float | None, pd.Timestamp | None]:
    """(last, previous, last date)."""
    s = s.dropna()
    if s.empty:
        return None, None, None
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return float(s.iloc[-1]), prev, s.index[-1]


def surprise_row(indicator: str) -> pd.Series | None:
    s = surprises()
    if s.empty:
        return None
    sub = s[s["indicator"] == indicator]
    return None if sub.empty else sub.iloc[-1]


def uf_known() -> tuple[pd.Series, date | None]:
    """Published UF path (BDE includes the window fixed by the latest CPI) and its last 9th."""
    uf = series("cl_uf")
    if uf.empty or not isinstance(uf.index, pd.DatetimeIndex):
        return pd.Series(dtype=float), None
    nines = uf.index[uf.index.day == 9]
    return uf, (nines.max().date() if len(nines) else None)


def last_cpi_month() -> date | None:
    s = series("cl_cpi_mm_hist")
    return None if s.empty else s.index[-1].date()


def today() -> date:
    return date.today()
