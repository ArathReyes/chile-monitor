from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from monitor.cl_calendar import add_business_days, next_business_day, spc_start
from monitor.models.backtest import (
    EOF_MEETING_KEYS,
    N_STEPS,
    SHORT_SWAPS,
    inputs_as_of,
    latest_eof_round,
    metrics,
    run_backtest,
    subtract_business_days,
    summarize,
)
from monitor.models.tpm_implied import FitConfig, SwapQuote, build_prior, build_steps, model_rates

START = date(2024, 1, 2)


def make_meetings(n: int = 12) -> list[tuple[date, date, str]]:
    out, d = [], add_business_days(date(2024, 3, 5), 0)
    for i in range(n):
        out.append((d, next_business_day(d), f"M{i}"))
        d = add_business_days(d + timedelta(days=42), 0)
    return out


def daily(start: date, end: date, fn) -> pd.Series:
    idx = pd.bdate_range(start, end)
    return pd.Series([fn(ts.date()) for ts in idx], index=idx, dtype=float)


def tpm_fn(meetings, levels, tpm0):
    def f(d: date) -> float:
        lvl = tpm0
        for (_, eff, _), x in zip(meetings, levels, strict=False):
            if d >= eff:
                lvl = x
        return lvl

    return f


def synthetic_world(levels: list[float], tpm0: float = 5.0):
    """Quotes generated from a known path; EOF survey equal to that path."""
    ms = make_meetings()
    end = ms[-1][1] + timedelta(days=30)
    tpm = daily(START, end, tpm_fn(ms, levels, tpm0))
    series = {"cl_tpm": tpm, "cl_tib": tpm.copy()}
    t = subtract_business_days(ms[0][0], 1)
    steps = build_steps(ms, t, N_STEPS)
    templ = [SwapQuote(sid, spc_start(t), 1.0, 360, tenor_days=n) for sid, n in SHORT_SWAPS]
    rates = model_rates(templ, tpm0, steps, np.array(levels[:N_STEPS]))
    for (sid, _), r in zip(SHORT_SWAPS, rates, strict=True):
        series[sid] = pd.Series([r], index=[pd.Timestamp(t)])
    sd = pd.Timestamp(t - timedelta(days=5))
    for k, x in zip(EOF_MEETING_KEYS, levels, strict=False):
        series[f"eof_tpm_{k}"] = pd.Series([x], index=[sd])
    return series, ms, t


# ---------------------------------------------------------------- look-ahead


def test_inputs_as_of_ignores_future_observations():
    series, ms, t = synthetic_world([5.25] * 12)
    base = inputs_as_of(t, series, ms)
    polluted = {k: s.copy() for k, s in series.items()}
    later = pd.Timestamp(t + timedelta(days=1))
    for sid in polluted:
        polluted[sid] = pd.concat([polluted[sid], pd.Series([99.0], index=[later])])
    polluted["eof_tpm_next"] = pd.concat(
        [series["eof_tpm_next"], pd.Series([0.5], index=[pd.Timestamp(t)])]
    )  # EOF dated t itself is not yet published
    got = inputs_as_of(t, polluted, ms)
    assert got.tpm0 == base.tpm0
    assert got.spread_bp == base.spread_bp
    assert [q.rate for q in got.quotes] == [q.rate for q in base.quotes]
    assert [q.start for q in got.quotes] == [q.start for q in base.quotes]
    np.testing.assert_allclose(got.prior, base.prior)


def test_stale_quotes_are_skipped():
    series, ms, t = synthetic_world([5.0] * 12)
    later = add_business_days(t, 6)  # > 7 calendar days after the only quote
    assert inputs_as_of(later, series, ms).quotes == []


def test_eof_round_uses_single_round_and_publication_lag():
    s = {
        "eof_tpm_next": pd.Series([5.0, 4.75], index=pd.to_datetime(["2024-01-05", "2024-01-20"])),
        "eof_tpm_rpm1": pd.Series([4.5], index=pd.to_datetime(["2024-01-05"])),
    }
    rnd = latest_eof_round(date(2024, 1, 20), s)
    assert rnd.survey_date == date(2024, 1, 5)
    rnd = latest_eof_round(date(2024, 1, 21), s)
    assert rnd.survey_date == date(2024, 1, 20)
    assert rnd.values == {"next": 4.75}  # rpm1 from an older round is not mixed in


# --------------------------------------------------------------- known path


def test_known_path_reproduces_actual_change():
    levels = [5.25, 5.5, 5.5, 5.25] + [5.25] * 8
    series, ms, _ = synthetic_world(levels)
    df = run_backtest(series, ms, (1,), FitConfig(lam=0.005, mu=0.0), targets=[0])
    row = df.iloc[0]
    assert row["skip_reason"] == ""
    assert row["actual_change_bp"] == pytest.approx(25.0)
    assert row["model_m1_bp"] == pytest.approx(25.0, abs=0.5)
    assert row["model_cum2_bp"] == pytest.approx(row["actual_cum2_bp"], abs=1.0)
    assert row["eof_m1_bp"] == pytest.approx(25.0)
    assert row["rw_m1_bp"] == 0.0


def test_target_not_first_step_is_skipped():
    series, ms, _ = synthetic_world([5.0] * 12)
    df = run_backtest(series, ms, (1,), targets=[1])  # quotes only exist before meeting 0
    assert np.isnan(df.iloc[0]["model_m1_bp"])
    assert df.iloc[0]["skip_reason"]


def test_prior_aligned_when_meeting_held_since_survey():
    ms = make_meetings()
    sd = ms[0][0] - timedelta(days=3)
    series = {
        "cl_tpm": daily(START, ms[3][1], lambda d: 5.0),
        "eof_tpm_next": pd.Series([4.75], index=[pd.Timestamp(sd)]),
        "eof_tpm_rpm1": pd.Series([4.5], index=[pd.Timestamp(sd)]),
    }
    t = ms[0][1] + timedelta(days=2)  # meeting 0 already held
    assert inputs_as_of(t, series, ms).prior[0] == pytest.approx(4.5)
    assert inputs_as_of(t, series, ms, align_prior=False).prior[0] == pytest.approx(4.75)


@pytest.mark.xfail(reason="build_prior maps EOF 'next' to steps[0] even after that meeting passed")
def test_build_prior_skips_meetings_held_since_survey():
    ms = make_meetings()
    sd = ms[0][0] - timedelta(days=3)
    steps = build_steps(ms, ms[0][1] + timedelta(days=2), N_STEPS)
    prior = build_prior(steps, 5.0, sd, {0: 4.75, 1: 4.5})
    assert prior[0] == pytest.approx(4.5)


# ------------------------------------------------------------------ metrics


def test_metrics_and_summarize_on_hand_frame():
    df = pd.DataFrame(
        {
            "horizon_bd": [1, 1, 1, 1],
            "regime": ["hiking", "hiking", "cutting", "cutting"],
            "actual_change_bp": [25.0, 0.0, -25.0, -50.0],
            "model_m1_bp": [20.0, 3.0, -40.0, np.nan],
        }
    )
    m = metrics(df["model_m1_bp"], df["actual_change_bp"])
    assert m["n"] == 3
    assert m["mae_bp"] == pytest.approx((5 + 3 + 15) / 3)
    assert m["rmse_bp"] == pytest.approx(np.sqrt((25 + 9 + 225) / 3))
    assert m["bias_bp"] == pytest.approx((-5 + 3 - 15) / 3)
    assert m["dir_hit"] == pytest.approx(1.0)  # +3 is inside the 5bp dead band
    assert m["bucket_hit"] == pytest.approx(2 / 3)  # -40 rounds to -50
    s = summarize(df)
    allm = s[(s.regime == "all") & (s.method == "model")].iloc[0]
    assert allm["n"] == 3
    cut = s[(s.regime == "cutting") & (s.method == "model")].iloc[0]
    assert cut["n"] == 1 and cut["mae_bp"] == pytest.approx(15.0)
