"""CPI seasonality by calendar month (pure)."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

DEFAULT_EXCLUDE = (2021, 2022)


def _clean(mm: pd.Series) -> pd.Series:
    s = mm.dropna().copy()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def seasonal_stats(
    mm: pd.Series,
    window_years: int = 10,
    exclude_years: Iterable[int] = DEFAULT_EXCLUDE,
    last_year: int | None = None,
) -> pd.DataFrame:
    """Per calendar month (1..12): mean, median, min, max, std and n of m/m changes over the
    last `window_years` complete years ending at `last_year` (default: last complete year),
    skipping `exclude_years`."""
    s = _clean(mm)
    if s.empty:
        return pd.DataFrame(columns=["mean", "median", "min", "max", "std", "n"])
    if last_year is None:
        last_year = s.index.max().year - (0 if s.index.max().month == 12 else 1)
    years = [
        y for y in range(last_year - window_years + 1, last_year + 1) if y not in set(exclude_years)
    ]
    sub = s[s.index.year.isin(years)]
    g = sub.groupby(sub.index.month)
    out = pd.DataFrame(
        {
            "mean": g.mean(),
            "median": g.median(),
            "min": g.min(),
            "max": g.max(),
            "std": g.std(),
            "n": g.size(),
        }
    )
    out.index.name = "month"
    return out.reindex(range(1, 13))


def heatmap_table(mm: pd.Series, since_year: int | None = None) -> pd.DataFrame:
    """Year (rows) x month (columns) table of m/m changes."""
    s = _clean(mm)
    if since_year:
        s = s[s.index.year >= since_year]
    df = pd.DataFrame({"year": s.index.year, "month": s.index.month, "v": s.values})
    return df.pivot(index="year", columns="month", values="v").reindex(columns=range(1, 13))
