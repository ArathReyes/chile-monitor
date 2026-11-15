"""Macro data surprises vs the EEE consensus and the Chile Macro Surprise Index (pure).

Mapping of EEE answers to reference months (EEE obs_date = 1st of the survey month):
  * CPI m/m: survey month m "en el mes" -> CPI of month m (released ~8th of m+1, after the
    survey's publication ~10th of m).
  * IMACEC y/y: survey month m "un mes atrás" -> IMACEC of month m-1 (released ~1st of m+1).
Actual values: CPI = INE official m/m (rounded, essentially unrevised). IMACEC y/y = computed
from the current (revised) index, rounded to 0.1, because first-print vintages before this app
started collecting are not available (observations_history only accumulates from now on).
There is no unemployment consensus in the BDE, so no unemployment surprises.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

INFLATION = ("cpi_mm",)
ACTIVITY = ("imacec_yoy", "imacec_nm_yoy")
# approximate release lag after the reference month (for placing surprises in time)
RELEASE_OFFSET = {
    "cpi_mm": pd.DateOffset(months=1, days=7),
    "imacec_yoy": pd.DateOffset(months=2),
    "imacec_nm_yoy": pd.DateOffset(months=2),
}
MIN_HISTORY = 12


def _clean(s: pd.Series) -> pd.Series:
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def build_surprises(
    indicator: str, actual: pd.Series, consensus: pd.Series, survey_to_ref_months: int
) -> pd.DataFrame:
    """Align consensus (by survey month) to actual (by reference month) and compute surprises.

    survey_to_ref_months: reference month = survey month + this (0 for CPI 'en el mes',
    -1 for IMACEC 'un mes atrás'). z-score uses the std of *previous* surprises only
    (expanding window, at least MIN_HISTORY), so there's no look-ahead.
    """
    a, c = _clean(actual), _clean(consensus)
    c_ref = c.copy()
    c_ref.index = c_ref.index + pd.DateOffset(months=survey_to_ref_months)
    df = pd.DataFrame({"actual": a, "consensus": c_ref}).dropna()
    df["surprise"] = df["actual"] - df["consensus"]
    prev_std = df["surprise"].expanding(MIN_HISTORY).std().shift(1)
    df["zscore"] = df["surprise"] / prev_std
    df["indicator"] = indicator
    df["reference_period"] = df.index.strftime("%Y-%m")
    df["release_date"] = (df.index + RELEASE_OFFSET.get(indicator, pd.DateOffset(months=1))).date
    df["consensus_date"] = (df.index - pd.DateOffset(months=survey_to_ref_months)).date
    return df.reset_index(drop=True)


def surprise_index(
    surprises: pd.DataFrame,
    indicators: tuple[str, ...],
    half_life_months: float = 3.0,
    freq: str = "MS",
) -> pd.Series:
    """Exponentially weighted sum of past z-scores (by release date), sampled monthly.

    Weight of a surprise released d days before t: 0.5 ** (d / (half_life_months * 30.4375)).
    Positive = higher inflation / stronger activity than expected.
    """
    s = surprises[surprises["indicator"].isin(indicators)].dropna(subset=["zscore"])
    if s.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    rel = pd.to_datetime(s["release_date"]).to_numpy()
    z = s["zscore"].to_numpy()
    grid = pd.date_range(
        pd.Timestamp(rel.min()).to_period("M").to_timestamp(),
        pd.Timestamp.today().normalize(),
        freq=freq,
    )
    hl_days = half_life_months * 30.4375
    vals = []
    for t in grid:
        mask = rel <= np.datetime64(t)
        age = (np.datetime64(t) - rel[mask]).astype("timedelta64[D]").astype(float)
        vals.append(float((z[mask] * 0.5 ** (age / hl_days)).sum()))
    return pd.Series(vals, index=grid)


def hit_rate(surprises: pd.DataFrame, tolerance: float = 0.05) -> pd.Series:
    """Share of releases where |surprise| <= tolerance (in the indicator's units), per indicator."""
    return surprises.groupby("indicator")["surprise"].apply(
        lambda x: (x.abs() <= tolerance + 1e-9).mean()
    )
