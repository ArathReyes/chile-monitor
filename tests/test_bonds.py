"""models/bonds.py: nominal conversions, supply buckets, concession, curve proxy, cash flows."""

from datetime import date

import pandas as pd
import pytest

from monitor.models import bonds as B


def test_unit_conversions():
    assert B.to_clp(50_000, "MM CLP", None) == 50_000e6
    assert B.to_clp(500, "M UF", 40_000.0) == 500e3 * 40_000
    assert B.to_clp(500, "M UF", None) is None  # no UF: no conversion, never guessed
    assert B.to_usd(950e6, 950.0) == pytest.approx(1e6)
    assert B.to_usd(1.0, None) is None
    with pytest.raises(ValueError):
        B.to_clp(1, "USD", None)


def test_add_nominal_amounts_uses_asof_fx():
    uf = pd.Series([40_000.0, 40_010.0], index=pd.to_datetime(["2026-11-12", "2026-11-13"]))
    fx = pd.Series([950.0, 960.0], index=pd.to_datetime(["2026-11-12", "2026-11-13"]))
    df = pd.DataFrame(
        {
            "auction_date": pd.to_datetime(["2026-11-12", "2026-11-17"]),
            "unit": ["M UF", "MM CLP"],
            "amount": [500.0, 50_000.0],
        }
    )
    out = B.add_nominal_amounts(df, "amount", uf, fx, "auction_date")
    assert out["amount_clp"].iloc[0] == pytest.approx(500e3 * 40_000)
    assert out["amount_usd"].iloc[0] == pytest.approx(500e3 * 40_000 / 950)
    # future auction: last known FX (13-Nov), reported in fx_date
    assert out["amount_usd"].iloc[1] == pytest.approx(50_000e6 / 960)
    assert out["fx_date"].iloc[1] == date(2026, 11, 13)


def test_tenor_buckets_and_weekly_supply():
    assert [B.tenor_bucket(x) for x in (0.9, 3.0, 6.99, 7, 12, 19.9, 29)] == [
        "0–3y",
        "3–7y",
        "3–7y",
        "7–12y",
        "12–20y",
        "12–20y",
        "20y+",
    ]
    cal = pd.DataFrame(
        {
            "auction_date": pd.to_datetime(
                ["2026-11-17", "2026-11-19", "2026-11-19", "2026-11-24"]
            ),
            "maturity": pd.to_datetime(["2027-11-23", "2032-07-15", "2027-10-15", "2055-11-01"]),
            "instrument_type": ["Letra", "BTP", "Letra", "BTU"],
            "amount_usd": [10.0, 20.0, 5.0, 7.0],
        }
    )
    w = B.weekly_supply(cal).set_index(["week", "segment", "bucket"])["amount_usd"]
    assert w[(date(2026, 11, 16), "Nominal (CLP)", "0–3y")] == 15.0  # Tue + Thu, same week
    assert w[(date(2026, 11, 16), "Nominal (CLP)", "3–7y")] == 20.0
    assert w[(date(2026, 11, 23), "Real (UF)", "20y+")] == 7.0
    assert w.sum() == 42.0


def test_concession_sign():
    assert B.concession_bp(5.30, 5.25) == pytest.approx(5.0)  # cleared above curve: cheap
    assert B.concession_bp(5.20, 5.25) == pytest.approx(-5.0)
    assert B.concession_bp(None, 5.0) is None and B.concession_bp(5.0, float("nan")) is None


def test_allocation_shares_treat_dash_as_zero():
    assert B.allocation_shares(500, None, 500) == (0.0, 100.0)
    assert B.allocation_shares(None, 1, 1) == (None, None)


def test_curve_yield_proxy():
    flat = {"b0": 5.0, "b1": 0.0, "b2": 0.0, "b3": 0.0, "tau1": 1.0, "tau2": 5.0}
    day = B.CurveDay(flat, {2.0: -0.10, 10.0: 0.10})
    assert B.curve_yield(6.0, day) == pytest.approx(5.0)  # spread interpolated: 0 at 6y
    assert B.curve_yield(1.0, day) == pytest.approx(4.9)  # flat below 2y
    assert B.curve_yield(30.0, day) == pytest.approx(5.1)  # flat beyond 10y
    assert B.curve_yield(5.0, B.CurveDay(None, {})) is None
    assert B.event_path({-1: 5.0, 0: 5.02, 5: 4.97}) == pytest.approx({-1: 0.0, 0: 2.0, 5: -3.0})


def test_coupon_dates_and_cashflow_calendar():
    ds = B.coupon_dates(
        date(2032, 7, 15), [1, 7], date(2021, 1, 1), date(2026, 11, 15), date(2027, 12, 31)
    )
    assert ds == [date(2027, 1, 15), date(2027, 7, 15)]
    assert B.coupon_dates(
        date(2031, 2, 28), [2, 8], None, date(2026, 1, 1), date(2026, 12, 31)
    ) == [date(2026, 2, 28), date(2026, 8, 28)]
    master = pd.DataFrame(
        {
            "nemo": ["BTP0530732", "BTP0001127"],
            "instrument_type": ["BTP", "Letra"],
            "coupon_pct": [5.3, 0.0],
            "coupon_freq": [2, 0],
            "coupon_months": ["1,7", None],
            "issue_date": pd.to_datetime(["2021-01-01", "2026-05-15"]),
            "maturity_date": pd.to_datetime(["2032-07-15", "2027-11-23"]),
        }
    )
    out = pd.DataFrame(
        {
            "nemo": ["BTP0530732", "BTP0001127"],
            "amount": [1000.0, 1400.0],
            "unit": ["MM CLP", "MM CLP"],
        }
    )
    cf = B.cashflow_calendar(master, out, date(2026, 11, 15), date(2027, 12, 31))
    cpn = cf[cf["kind"] == "coupon"]
    assert list(cpn["date"]) == [date(2027, 1, 15), date(2027, 7, 15)]
    assert cpn["amount"].iloc[0] == pytest.approx(1000 * 5.3 / 100 / 2)
    pr = cf[cf["kind"] == "principal"]
    assert list(pr["nemo"]) == ["BTP0001127"] and pr["amount"].iloc[0] == 1400.0


def test_btc_terciles_and_pace():
    t = B.btc_terciles(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, None]))
    assert list(t[:6]) == ["Low", "Low", "Mid", "Mid", "High", "High"] and pd.isna(t.iloc[6])
    assert B.straight_line_pace(2026, 365.0, date(2026, 7, 2)) == pytest.approx(182 * 365 / 364)
