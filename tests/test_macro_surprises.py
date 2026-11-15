import numpy as np
import pandas as pd
import pytest

from monitor.models.macro_surprises import build_surprises, hit_rate, surprise_index


def test_build_surprises_alignment_and_zscore():
    idx = pd.date_range("2020-01-01", periods=30, freq="MS")
    actual = pd.Series(0.3, index=idx)
    actual.iloc[-1] = 0.6
    consensus = pd.Series(0.3, index=idx)  # CPI: survey month == reference month
    rng = np.random.default_rng(0)
    actual.iloc[:-1] += rng.normal(0, 0.1, 29).round(1)
    s = build_surprises("cpi_mm", actual, consensus, 0)
    assert len(s) == 30 and s["surprise"].iloc[-1] == pytest.approx(0.3)
    assert s["zscore"].iloc[:12].isna().all()  # not enough prior history
    assert s["zscore"].iloc[-1] > 1
    # IMACEC: survey month m answers for m-1
    imc = build_surprises(
        "imacec_yoy",
        pd.Series([2.0], index=[pd.Timestamp("2026-08-01")]),
        pd.Series([1.5], index=[pd.Timestamp("2026-09-01")]),
        -1,
    )
    assert (
        imc["surprise"].iloc[0] == pytest.approx(0.5)
        and imc["reference_period"].iloc[0] == "2026-08"
    )


def test_surprise_index_decay():
    s = pd.DataFrame(
        {
            "indicator": ["cpi_mm"],
            "zscore": [2.0],
            "release_date": [pd.Timestamp("2025-01-01").date()],
        }
    )
    idx = surprise_index(s, ("cpi_mm",), half_life_months=3)
    v0 = idx.loc[pd.Timestamp("2025-01-01")]
    v3 = idx.loc[pd.Timestamp("2025-04-01")]
    assert v0 == pytest.approx(2.0)
    assert v3 == pytest.approx(2.0 * 0.5 ** (90 / (3 * 30.4375)))
    assert surprise_index(s, ("imacec_yoy",)).empty


def test_hit_rate():
    s = pd.DataFrame({"indicator": ["cpi_mm"] * 4, "surprise": [0.0, 0.1, -0.05, 0.0]})
    assert hit_rate(s).loc["cpi_mm"] == pytest.approx(0.75)
