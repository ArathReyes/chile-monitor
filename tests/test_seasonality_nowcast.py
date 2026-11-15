from datetime import date

import numpy as np
import pandas as pd
import pytest

from monitor.models.inflation_nowcast import (
    implied_12m,
    next_months,
    nowcast_table,
    survey_for_month,
)
from monitor.models.seasonality import heatmap_table, seasonal_stats

PROFILE = np.array([0.8, 0.2, 0.5, 0.4, 0.3, 0.1, 0.4, 0.3, 0.6, 0.5, 0.2, 0.1])


def synthetic(years=range(2012, 2026)):
    idx = pd.date_range(f"{years[0]}-01-01", f"{years[-1]}-12-01", freq="MS")
    vals = np.tile(PROFILE, len(years))
    s = pd.Series(vals, index=idx)
    s[s.index.year.isin([2021, 2022])] += 1.0  # outlier years
    return s


def test_seasonal_profile_recovered_exactly():
    st = seasonal_stats(synthetic(), window_years=10)
    np.testing.assert_allclose(st["mean"].values, PROFILE)
    np.testing.assert_allclose(st["std"].values, 0, atol=1e-12)
    assert st["n"].iloc[0] == 8  # 10 years minus 2 excluded
    with_outliers = seasonal_stats(synthetic(), window_years=10, exclude_years=())
    assert (with_outliers["mean"].values > PROFILE).all()


def test_heatmap_shape():
    h = heatmap_table(synthetic(), since_year=2020)
    assert h.shape == (6, 12) and h.loc[2025, 1] == pytest.approx(0.8)


def test_survey_mapping_and_nowcast():
    eee_this = pd.Series([0.4], index=pd.to_datetime(["2026-11-01"]))
    eee_next = pd.Series([0.2], index=pd.to_datetime(["2026-11-01"]))
    assert survey_for_month(date(2026, 11, 1), eee_this, eee_next, date(2026, 11, 15))[0] == 0.4
    assert survey_for_month(date(2026, 12, 1), eee_this, eee_next, date(2026, 11, 15))[0] == 0.2
    assert (
        survey_for_month(date(2026, 11, 1), eee_this, eee_next, date(2026, 11, 5))[0] is None
    )  # unpublished
    hist = synthetic(range(2015, 2026))
    hist.loc[pd.Timestamp("2025-11-01")] = 0.3
    st = seasonal_stats(hist)
    empty = pd.Series(dtype=float)
    t = nowcast_table(
        [date(2026, 11, 1), date(2026, 12, 1)],
        hist,
        st,
        eee_this,
        eee_next,
        empty,
        empty,
        empty,
        date(2026, 11, 15),
    )
    row = t.iloc[0]
    # Nov-2025 = 0.3 also enters the 10-year window: (7 * 0.2 + 0.3) / 8
    assert row["seasonal_avg"] == pytest.approx(0.2125) and row["last_year"] == pytest.approx(0.3)
    assert row["eee"] == 0.4 and row["market"] is None
    assert row["spread_bp"] == pytest.approx(18.75) and bool(row["disagree"])


def test_implied_12m_and_next_months():
    hist = pd.Series(0.25, index=pd.date_range("2025-11-01", "2026-10-01", freq="MS"))
    v = implied_12m(hist, {date(2026, 11, 1): 0.5}, {12: 0.1}, date(2026, 12, 1))
    expected = ((1.0025**10) * 1.005 * 1.001 - 1) * 100
    assert v == pytest.approx(expected)
    assert implied_12m(hist, {}, {}, date(2026, 12, 1)) is None
    assert next_months(date(2026, 10, 1)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
    ]
