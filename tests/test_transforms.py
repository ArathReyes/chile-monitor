import numpy as np
import pandas as pd
import pytest

from monitor.models.transforms import (
    contributions_yoy,
    estimate_laspeyres_weights,
    mom,
    qoq_saar,
    rolling_sum,
    saar,
    yoy,
)

IDX = pd.date_range("2020-01-01", periods=36, freq="MS")


def test_mom_yoy_constant_growth():
    s = pd.Series(100 * 1.01 ** np.arange(36), index=IDX)
    assert mom(s).iloc[1:].round(10).eq(1.0).all()
    assert yoy(s).iloc[12] == pytest.approx((1.01**12 - 1) * 100)
    assert yoy(s).iloc[:12].isna().all()


def test_gaps_are_not_filled():
    s = pd.Series(100 * 1.01 ** np.arange(36), index=IDX).drop(IDX[5])
    m = mom(s)
    assert np.isnan(m.loc[IDX[6]])  # previous month missing -> NaN, not a 2-month change


def test_saar_and_qoq():
    s = pd.Series(100 * 1.01 ** np.arange(36), index=IDX)
    assert saar(s, 3).iloc[-1] == pytest.approx((1.01**12 - 1) * 100)
    assert saar(s, 6).iloc[-1] == pytest.approx((1.01**12 - 1) * 100)
    q = pd.Series(
        100 * 1.02 ** np.arange(8), index=pd.date_range("2020-01-01", periods=8, freq="QS")
    )
    assert qoq_saar(q).iloc[-1] == pytest.approx((1.02**4 - 1) * 100)


def test_contributions_sum_to_headline():
    rng = np.random.default_rng(0)
    comps = pd.DataFrame(
        {k: 100 * np.cumprod(1 + rng.normal(0.003, 0.004, 36)) for k in "abc"}, index=IDX
    )
    w = {"a": 0.5, "b": 0.3, "c": 0.2}
    head = (comps * pd.Series(w)).sum(axis=1)
    contrib = contributions_yoy(comps, w)
    np.testing.assert_allclose(contrib.sum(axis=1).iloc[12:], yoy(head).iloc[12:], atol=1e-10)


def test_estimate_laspeyres_weights_recovers_truth():
    rng = np.random.default_rng(1)
    comps = pd.DataFrame(
        {k: 100 * np.cumprod(1 + rng.normal(0.003, 0.01, 36)) for k in "abcd"}, index=IDX
    )
    w = {"a": 0.4, "b": 0.3, "c": 0.2, "d": 0.1}
    head = (comps * pd.Series(w)).sum(axis=1).round(2)
    est, resid = estimate_laspeyres_weights(head, comps)
    for k, v in w.items():
        assert est[k] == pytest.approx(v, abs=2e-3)
    assert resid < 0.02


def test_rolling_sum():
    s = pd.Series(1.0, index=IDX)
    assert rolling_sum(s, 12).iloc[-1] == 12
