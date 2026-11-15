"""Shared time-series transforms (pure). Indices in, percent changes out; never fills gaps."""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear


def _monthly(s: pd.Series) -> pd.Series:
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def _lag(s: pd.Series, months: int) -> pd.Series:
    """Value `months` calendar months earlier (NaN when that month is missing)."""
    shifted = s.copy()
    shifted.index = shifted.index + pd.DateOffset(months=months)
    return shifted.reindex(s.index)


def mom(index: pd.Series) -> pd.Series:
    """Month-on-month % change."""
    s = _monthly(index)
    return (s / _lag(s, 1) - 1) * 100


def yoy(index: pd.Series) -> pd.Series:
    """Change vs the same date a year earlier, % (works for monthly and quarterly-start dates)."""
    s = _monthly(index)
    return (s / _lag(s, 12) - 1) * 100


def saar(index: pd.Series, window: int) -> pd.Series:
    """Annualized % change of the `window`-month average vs the previous `window` months
    (3m/3m SAAR with window=3, 6m/6m with window=6). Use on seasonally adjusted indices."""
    s = _monthly(index)
    avg = s.rolling(window).mean()
    # require consecutive months for both windows
    full = s.index.to_series().diff().dt.days.le(31).rolling(2 * window - 1).sum() == 2 * window - 1
    prev = _lag(avg, window)
    out = ((avg / prev) ** (12 / window) - 1) * 100
    return out.where(full)


def qoq_saar(index: pd.Series) -> pd.Series:
    """Quarter-on-quarter annualized % change of a quarterly SA index (dates = quarter starts)."""
    s = _monthly(index)
    return ((s / _lag(s, 3)) ** 4 - 1) * 100


def contributions_yoy(components: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    """Contribution of each component to headline y/y, in pp: w_i * I_i,t-12 / I_t-12 * y/y_i.

    Exact for a fixed-weight (Laspeyres) index whose components share its base (sum of
    contributions = headline y/y). Weights must sum to 1.
    """
    comp = components.copy()
    comp.index = pd.to_datetime(comp.index)
    w = pd.Series(weights)[comp.columns]
    lagged = comp.apply(lambda c: _lag(c, 12))
    head_lag = (lagged * w).sum(axis=1, min_count=len(w))
    return (lagged.mul(w, axis=1).div(head_lag, axis=0)) * ((comp / lagged - 1) * 100)


def moving_average(s: pd.Series, window: int) -> pd.Series:
    return _monthly(s).rolling(window).mean()


def rolling_sum(s: pd.Series, window: int) -> pd.Series:
    return _monthly(s).rolling(window).sum()


def estimate_laspeyres_weights(headline: pd.Series, components: pd.DataFrame) -> tuple[dict, float]:
    """Recover fixed weights w (>=0, sum 1) with headline_t = sum_i w_i component_i,t.

    Returns (weights, max absolute residual in index points). A residual near the publication
    rounding (~0.01) confirms a Laspeyres structure.
    """
    df = pd.concat([headline.rename("_h"), components], axis=1).dropna()
    a = df[components.columns].to_numpy()
    y = df["_h"].to_numpy()
    lam = 1e3
    sol = lsq_linear(np.vstack([a, lam * np.ones(a.shape[1])]), np.append(y, lam), bounds=(0, 1))
    w = sol.x
    resid = float(np.abs(a @ w - y).max())
    return dict(zip(components.columns, w.tolist(), strict=True)), resid
