from datetime import date

import pandas as pd
import pytest

from monitor.models.uf import (
    breakeven_monthly_cpi,
    cpi_month_for_period,
    known_uf_path,
    path_with_scenario,
    period_anchor,
    projected_uf_path,
    uf_accrual_pct,
    uf_carry,
)


@pytest.fixture
def real(load_fixture):
    fx = load_fixture("uf_cpi_2026-09_to_11.json")
    uf = pd.Series(fx["uf"]).rename(index=pd.Timestamp)
    cpi = {pd.Timestamp(k).date(): v for k, v in fx["cpi_mm"].items()}
    return uf, cpi


def test_reproduces_published_uf_exactly(real):
    uf, cpi = real
    for anchor in (date(2026, 9, 9), date(2026, 10, 9)):
        ref = cpi_month_for_period(anchor)
        path = known_uf_path(float(uf[pd.Timestamp(anchor)]), anchor, cpi[ref])
        actual = uf.reindex(path.index)
        assert (path - actual).abs().max() <= 0.01 + 1e-9
        assert (path - actual).abs().max() == pytest.approx(0.0)


def test_projection_chains_periods(real):
    uf, cpi = real
    proj = projected_uf_path(date(2026, 9, 9), float(uf[pd.Timestamp("2026-09-09")]), cpi, months=2)
    assert proj.index.max() == pd.Timestamp("2026-11-09")
    assert (proj - uf.reindex(proj.index)).abs().max() == pytest.approx(0.0)
    with pytest.raises(ValueError):
        projected_uf_path(date(2026, 9, 9), 40000.0, {}, months=1)


def test_zero_cpi_is_flat():
    p = known_uf_path(40000.0, date(2026, 10, 9), 0.0)
    assert (p == 40000.0).all() and len(p) == 31


def test_period_helpers():
    assert period_anchor(date(2026, 11, 15)) == date(2026, 11, 9)
    assert period_anchor(date(2026, 11, 9)) == date(2026, 10, 9)
    assert cpi_month_for_period(date(2026, 11, 9)) == date(2026, 10, 1)


def test_carry_symmetry_and_accrual():
    flat = known_uf_path(40000.0, date(2026, 10, 9), 0.0)
    r = uf_carry(flat, date(2026, 10, 10), date(2026, 11, 9), 5.0, 5.0)
    assert r.diff_bp == pytest.approx(0.0) and r.uf_accrual_pct == pytest.approx(0.0)
    up = known_uf_path(40000.0, date(2026, 10, 9), 0.3)
    assert uf_accrual_pct(up, date(2026, 10, 10), date(2026, 11, 9)) > 0
    low_real = uf_carry(up, date(2026, 10, 10), date(2026, 11, 9), 2.0, 5.0)
    high_real = uf_carry(up, date(2026, 10, 10), date(2026, 11, 9), 5.0, 2.0)
    assert low_real.diff_bp < high_real.diff_bp


def test_breakeven_cpi_zeroes_carry():
    anchor = pd.Series([40000.0], index=[pd.Timestamp("2026-10-09")])
    known = pd.concat([anchor, known_uf_path(40000.0, date(2026, 10, 9), 0.4)])
    start, end = date(2026, 10, 10), date(2027, 2, 9)
    m = breakeven_monthly_cpi(known, date(2026, 11, 9), start, end, 2.0, 5.0)
    scenario = {date(2026, 10, 1): m, date(2026, 11, 1): m, date(2026, 12, 1): m}
    path = path_with_scenario(known, date(2026, 11, 9), scenario, 3)
    assert abs(uf_carry(path, start, end, 2.0, 5.0).diff_bp) < 0.2  # UF rounds to 0.01 CLP
    assert 0.1 < m < 0.4
    with pytest.raises(ValueError):  # inside the known window: nothing to solve
        breakeven_monthly_cpi(known, date(2026, 11, 9), start, date(2026, 11, 1), 2.0, 5.0)
