from datetime import date, timedelta

import numpy as np
import pytest

from monitor.cl_calendar import business_days, following, next_business_day
from monitor.models.tpm_implied import (
    Anchor,
    FitConfig,
    Step,
    SwapQuote,
    build_prior,
    build_steps,
    change_probabilities,
    convert_basis,
    fit_path,
    model_rates,
)

T0 = date(2026, 11, 13)


def step_at(d: date) -> Step:
    d = following(d)
    return Step(d, next_business_day(d))


def quotes_from(rates: dict[int, float], basis: int = 360) -> list[SwapQuote]:
    return [SwapQuote(f"{n}d", T0, r, basis, tenor_days=n) for n, r in rates.items()]


def brute_force_rate(start: date, mat: date, rate_fn) -> float:
    """Independent implementation: compound business-day rates over calendar days."""
    g = 1.0
    for d in business_days(start, mat):
        n = (next_business_day(d) - d).days
        g *= 1 + rate_fn(d) * n / 36000
    return (g - 1) * 36000 / (mat - start).days


def test_convert_basis_round_trip():
    r = 4.873
    assert convert_basis(convert_basis(r, 365, 360), 360, 365) == pytest.approx(r)
    assert convert_basis(5.0, 365, 360) == pytest.approx(5.0 * 360 / 365)


def test_flat_path_reprices_flat_curve():
    steps = [step_at(T0 + timedelta(days=d)) for d in (32, 74, 130)]
    q = quotes_from({90: 0, 180: 0, 360: 0})
    flat = model_rates(q, 4.5, steps, np.array([4.5, 4.5, 4.5]))
    # daily compounding lifts a flat 4.5% above 4.5 on a simple ACT/360 quote:
    # 360d ~ e^0.045 - 1 = 4.603%; shorter tenors less
    assert (
        np.all(np.diff(flat) > 0) and flat[0] > 4.5 and flat[-1] == pytest.approx(4.603, abs=0.01)
    )
    market = [
        SwapQuote(x.name, x.start, r, 360, tenor_days=x.tenor_days)
        for x, r in zip(q, flat, strict=True)
    ]
    fit = fit_path(market, 4.5, steps, cfg=FitConfig(lam=0, mu=0))
    assert np.allclose(fit.path["implied_tpm"], 4.5, atol=1e-6)
    assert fit.rmse_bp < 1e-6


def test_known_hike_reproduces_90d():
    s = step_at(T0 + timedelta(days=32))
    q = SwapQuote("90d", T0, 0, 360, tenor_days=90)
    got = model_rates([q], 4.5, [s], np.array([4.75]))[0]
    expected = brute_force_rate(T0, q.maturity, lambda d: 4.75 if d >= s.effective_date else 4.5)
    assert got == pytest.approx(expected, abs=1e-10)
    assert 4.5 < got < 4.75


def test_exact_fit_when_square_and_unregularized():
    steps = [step_at(T0 + timedelta(days=d)) for d in (30, 100, 200)]
    true = np.array([4.75, 5.0, 4.75])
    q = quotes_from({90: 0, 180: 0, 360: 0})
    mk = model_rates(q, 4.5, steps, true)
    market = [
        SwapQuote(x.name, x.start, r, 360, tenor_days=x.tenor_days)
        for x, r in zip(q, mk, strict=True)
    ]
    fit = fit_path(market, 4.5, steps, cfg=FitConfig(lam=0, mu=0))
    assert np.allclose(fit.path["implied_tpm"], true, atol=1e-6)
    assert fit.quality == "good"


def test_semiannual_2y_flat_curve():
    q = SwapQuote("2y", T0, 0, 365, tenor_years=2)
    r = model_rates([q], 4.5, [step_at(T0 + timedelta(days=40))], np.array([4.5]))[0]
    # semiannual ACT/360 par rate on a flat daily-compounded 4.5% curve: just above 4.5
    assert 4.5 < r < 4.6


def test_regularization_pulls_to_prior_and_anchor():
    steps = [step_at(T0 + timedelta(days=d)) for d in (32, 74, 130, 200)]
    q = quotes_from({90: 4.6})
    prior = np.array([4.75, 4.75, 4.75, 4.75])
    fit = fit_path(q, 4.5, steps, prior=prior, cfg=FitConfig(lam=1.0, mu=0))
    assert fit.path["implied_tpm"].iloc[-1] == pytest.approx(4.75, abs=0.01)
    anch = [Anchor(steps[0].effective_date, 4.5, weight=1e4)]
    fit2 = fit_path(q, 4.5, steps, prior=prior, anchors=anch, cfg=FitConfig(lam=0.01, mu=0))
    assert fit2.path["implied_tpm"].iloc[0] == pytest.approx(4.5, abs=0.005)


def test_change_probabilities():
    assert change_probabilities(9.0) == pytest.approx({0: 0.64, 25: 0.36})
    assert change_probabilities(-30.0) == pytest.approx({-50: 0.2, -25: 0.8})
    assert change_probabilities(0.0) == {0: 1.0}


def test_build_steps_pads_with_grid():
    meetings = [
        (date(2026, 12, 15), date(2026, 12, 16), "Dec"),
        (date(2026, 10, 27), date(2026, 10, 28), "Oct"),
    ]
    steps = build_steps(meetings, T0, 4)
    assert [s.scheduled for s in steps] == [True, False, False, False]
    assert steps[0].label == "Dec"
    assert steps[1].effective_date > steps[0].effective_date


def test_build_prior_interpolates():
    steps = [
        Step(date(2026, 12, 15), date(2026, 12, 16)),
        Step(date(2027, 1, 26), date(2027, 1, 27)),
    ]
    p = build_prior(
        steps, 4.5, date(2026, 11, 11), meeting_points={0: 4.75}, horizon_points={365: 5.0}
    )
    assert p[0] == pytest.approx(4.75)
    assert 4.75 < p[1] < 5.0
    assert np.all(build_prior(steps, 4.5, None) == 4.5)


def test_spc_start_is_t_plus_2_business_days():
    from monitor.cl_calendar import spc_start

    assert spc_start(date(2026, 11, 13)) == date(2026, 11, 17)  # Fri -> Tue
    assert spc_start(date(2026, 12, 29)) == date(2027, 1, 4)  # skips 31-Dec bank holiday, 1-Jan
