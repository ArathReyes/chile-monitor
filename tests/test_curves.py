import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from monitor import storage
from monitor.config import load_series
from monitor.models import curves
from monitor.sources.bcch import parse_get_series

FIX = Path(__file__).parent / "fixtures"


def _load(name: str) -> pd.Series:
    df = parse_get_series(json.loads((FIX / name).read_text(encoding="utf-8")))
    return df.set_index(pd.to_datetime(df["obs_date"]))["value"]


# ------------------------------------------------------------------ registry


def test_registry_curve_points():
    specs = load_series().series
    nom_swap = curves.points_from_specs(specs, "nom_swap")
    assert [p.tenor for p in nom_swap] == [0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0]
    assert {p.basis for p in nom_swap if p.tenor <= 1} == {360}
    assert {p.basis for p in nom_swap if p.tenor > 1} == {365}
    assert [p.tenor for p in curves.points_from_specs(specs, "nom_bond")] == [1, 2, 5, 10]
    assert [p.tenor for p in curves.points_from_specs(specs, "real_bond")] == [1, 2, 5, 10, 20, 30]
    for s in specs:
        if s.curve:
            assert s.tab == "curves_rates" and s.tenor_years is not None and s.verified
        if s.derived:
            assert s.source == "derived" and s.unit == "bp"
            a, b = curves.derived_legs(s.source_code)
            assert load_series().by_id(a).tenor_years == s.tenor_years
            assert load_series().by_id(b).tenor_years == s.tenor_years


def test_no_spread_tenor_without_both_legs():
    ids = {s.series_id for s in load_series().series if s.derived}
    # no nominal 20y/30y anywhere -> no 20y/30y bond breakeven or swap spread
    assert not {i for i in ids if i.endswith(("_20y", "_30y"))}


# ------------------------------------------------------------------ parsers


def test_fixture_parses_with_gaps():
    s = _load("bcch_getseries_cl_bcp_2y_2026-10_11.json")
    assert s.index.max() == pd.Timestamp("2026-11-11")
    assert s.notna().all()  # "NaN" rows are dropped, never filled
    assert 15 <= len(s) <= 25


# ------------------------------------------------------------------ as-of


def test_value_asof_gaps_and_holidays():
    s = pd.Series(
        [5.0, 5.1, 5.3],
        index=pd.to_datetime(["2026-09-15", "2026-09-17", "2026-09-21"]),
    )
    # 18-19 Sep are Chilean holidays, 20 Sep is a Sunday: falls back to the 17th
    a = curves.value_asof(s, date(2026, 9, 20))
    assert a == curves.AsOf(5.1, date(2026, 9, 17))
    assert curves.value_asof(s, date(2026, 9, 21)).value == 5.3
    assert curves.value_asof(s, date(2026, 9, 14)) is None
    # NaN rows are skipped, not returned
    s2 = pd.concat([s, pd.Series([float("nan")], index=pd.to_datetime(["2026-09-22"]))])
    assert curves.value_asof(s2, date(2026, 9, 22)).obs_date == date(2026, 9, 21)


def test_bd_age_skips_holidays_and_weekends():
    # Thu 17 Sep 2026 -> Mon 21 Sep: 18 (holiday), 19 (holiday/Sat), 20 (Sun) -> 1 bd
    assert curves.bd_age(date(2026, 9, 17), date(2026, 9, 21)) == 1
    assert curves.bd_age(date(2026, 11, 13), date(2026, 11, 15)) == 0  # Fri -> Sun
    assert curves.bd_age(date(2026, 11, 13), date(2026, 11, 13)) == 0


def test_curve_asof_stale_and_hidden():
    idx = pd.to_datetime(["2025-12-24", "2026-11-05", "2026-11-12"])
    panel = pd.DataFrame(
        {1.0: [4.4, None, None], 2.0: [5.0, 5.05, None], 5.0: [5.5, 5.6, 5.65]}, index=idx
    )
    c = curves.curve_asof(panel, date(2026, 11, 13))
    assert list(c["tenor"]) == [2.0, 5.0]  # the 1y (Dec 2025) is hidden, not interpolated
    row2 = c.set_index("tenor").loc[2.0]
    assert row2["obs_date"] == date(2026, 11, 5) and bool(row2["stale"])  # 6 bd old
    assert not bool(c.set_index("tenor").loc[5.0, "stale"])


def test_horizon_targets():
    ref = date(2026, 11, 13)
    assert curves.horizon_target(ref, "1D") == date(2026, 11, 12)
    assert curves.horizon_target(ref, "1W") == date(2026, 11, 6)
    assert curves.horizon_target(ref, "1M") == date(2026, 10, 13)
    assert curves.horizon_target(ref, "1Y") == date(2025, 11, 13)
    with pytest.raises(ValueError):
        curves.horizon_target(ref, "2D")


# ------------------------------------------------------------------ spreads


def test_curve_panel_converts_360_to_365():
    wide = pd.DataFrame({"a": [3.6], "b": [4.0]}, index=pd.to_datetime(["2026-11-13"]))
    p = curves.curve_panel(wide, [curves.CurvePoint("a", 1.0, 360), curves.CurvePoint("b", 2.0)])
    assert p.loc["2026-11-13", 1.0] == pytest.approx(3.65)
    assert p.loc["2026-11-13", 2.0] == 4.0


def test_spreads_only_where_both_legs_exist():
    a = pd.Series([5.0, 5.1, 5.2], index=pd.to_datetime(["2026-11-10", "2026-11-11", "2026-11-12"]))
    b = pd.Series([2.0, 2.3], index=pd.to_datetime(["2026-11-10", "2026-11-12"]))
    sp = curves.spread_series(a, b)
    assert list(sp.index) == list(pd.to_datetime(["2026-11-10", "2026-11-12"]))
    assert sp.tolist() == pytest.approx([300.0, 290.0])


def test_compute_derived_converts_and_aligns():
    nom = _load("bcch_getseries_cl_spc_nom_360d_2026-10_11.json")
    bond = _load("bcch_getseries_cl_bcp_2y_2026-10_11.json")
    wide = pd.concat({"n": nom, "b": bond}, axis=1)
    spec = SimpleNamespace(series_id="x", source_code="n - b")
    out = curves.compute_derived(wide, [spec], {"n": 360, "b": 365})
    both = nom.index.intersection(bond.index)
    assert len(out) == len(both)
    d = pd.Timestamp(both[-1])
    exp = (nom[d] * 365 / 360 - bond[d]) * 100
    assert out.set_index("obs_date").loc[d, "value"] == pytest.approx(exp)
    with pytest.raises(ValueError):
        curves.derived_legs("a-b")


def test_spread_panel_common_tenors():
    idx = pd.to_datetime(["2026-11-12", "2026-11-13"])
    a = pd.DataFrame({2.0: [5.0, 5.1], 20.0: [6.0, None]}, index=idx)
    b = pd.DataFrame({2.0: [2.0, None], 5.0: [2.5, 2.6]}, index=idx)
    sp = curves.spread_panel(a, b)
    assert list(sp.columns) == [2.0]
    assert sp[2.0].dropna().tolist() == pytest.approx([300.0])


# ------------------------------------------------------------------ storage


def test_curve_tables():
    c = storage.connect(":memory:")
    try:
        assert storage.read_bond_quotes(c).empty
        df = pd.DataFrame(
            [("nom_swap", "2026-11-13", '{"b0": 5}', 1.2, 8, None),
             ("real_bond", "2026-11-13", None, None, 3, "3 points < 4")],
            columns=storage.CURVE_FIT_COLUMNS,
        )  # fmt: skip
        assert storage.upsert_curve_fits(c, df) == 2
        assert storage.upsert_curve_fits(c, df) == 2  # idempotent
        got = storage.read_curve_fits(c)
        assert len(got) == 2
        assert storage.last_curve_fit_date(c, "nom_swap") == date(2026, 11, 13)
        assert storage.read_curve_fits(c, "real_bond")["note"].iloc[0] == "3 points < 4"
    finally:
        c.close()


# ================================================================ Phase 2 models

TRUE = {"b0": 5.0, "b1": -1.0, "b2": 1.5, "b3": -0.8, "tau1": 1.5, "tau2": 8.0}
GRID = [0.25, 0.5, 1, 2, 3, 4, 5, 7, 10, 15, 20, 30]


def test_nss_recovers_known_parameters():
    y = curves.nss_yield(np.array(GRID, dtype=float), TRUE)
    fit = curves.fit_nss(GRID, y)
    assert fit.ok and fit.quality == "good"
    assert fit.rmse_bp < 0.1
    for k in ("b0", "b1", "tau1", "tau2"):
        assert fit.params[k] == pytest.approx(TRUE[k], rel=0.05, abs=0.05)
    dense = np.linspace(0.25, 30, 60)
    assert np.abs(curves.nss_yield(dense, fit.params) - curves.nss_yield(dense, TRUE)).max() < 0.005


def test_nss_skips_with_reason_and_uses_previous_fit():
    fit = curves.fit_nss([2, 5, 10], [5.1, 5.6, 6.0])
    assert not fit.ok and fit.note == "3 points < 4" and fit.quality == "skipped"
    y = curves.nss_yield(np.array(GRID, dtype=float), TRUE)
    warm = curves.fit_nss(GRID, y, prev=TRUE)
    assert warm.rmse_bp < 0.1


def test_fit_history_rows():
    idx = pd.to_datetime(["2026-11-12", "2026-11-13"])
    y = curves.nss_yield(np.array([1, 2, 3, 5, 10], dtype=float), TRUE)
    panel = pd.DataFrame(
        [y, [y[0], np.nan, np.nan, y[3], y[4]]], index=idx, columns=[1.0, 2.0, 3.0, 5.0, 10.0]
    )
    h = curves.fit_history(panel, "nom_swap")
    assert list(h.columns) == storage.CURVE_FIT_COLUMNS
    assert h["n_points"].tolist() == [5, 3]
    assert isinstance(h["params_json"].iloc[0], str) and pd.isna(h["params_json"].iloc[1])


def test_forwards():
    assert curves.forward_rate(5.0, 1, 5.0, 2) == pytest.approx(5.0)
    # (1.06^2 / 1.05) - 1
    assert curves.forward_rate(5.0, 1, 6.0, 2) == pytest.approx((1.06**2 / 1.05 - 1) * 100)
    flat = {"b0": 4.0, "b1": 0.0, "b2": 0.0, "b3": 0.0, "tau1": 1.0, "tau2": 5.0}
    assert curves.nss_forward(flat, 5, 10) == pytest.approx(4.0)
    today = pd.DataFrame({"tenor": [2.0, 5.0, 10.0], "value": [5.0, 5.5, 6.0]})
    assert curves.observed_forward(today, 1.0, 2.0) is None
    assert curves.observed_forward(today, 5.0, 10.0) == pytest.approx(
        curves.forward_rate(5.5, 5, 6.0, 10)
    )
    with pytest.raises(ValueError):
        curves.forward_rate(5, 2, 5, 1)


def test_slopes_and_flies_need_all_legs():
    idx = pd.to_datetime(["2026-11-12", "2026-11-13"])
    p = pd.DataFrame({2.0: [5.0, 5.1], 5.0: [5.5, np.nan], 10.0: [6.0, 6.2]}, index=idx)
    assert curves.slope(p, 2.0, 10.0).tolist() == pytest.approx([100.0, 110.0])
    assert curves.fly(p, 2.0, 5.0, 10.0).tolist() == pytest.approx([0.0])
    assert curves.slope(p, 10.0, 20.0).empty  # no 20y: n/a, never extrapolated


def test_flat_curve_has_zero_rolldown():
    flat = {"b0": 5.0, "b1": 0.0, "b2": 0.0, "b3": 0.0, "tau1": 1.0, "tau2": 5.0}
    for tenor in (1.0, 2.0, 5.0, 10.0):
        rolled = float(curves.nss_yield(tenor - 0.25, flat)[0])
        cr = curves.carry_roll(tenor, 5.0, rolled, funding_pct=5.0, h_years=0.25)
        assert cr.roll_bp == pytest.approx(0.0, abs=1e-9)
        assert cr.carry_bp == pytest.approx(0.0, abs=1e-9)
        assert cr.total_bp == pytest.approx(0.0, abs=1e-9)
    lin = curves.interp_linear([2, 5, 10], [5.0, 5.0, 5.0], 4.75)
    assert curves.carry_roll(5.0, 5.0, lin, 4.0, 0.25).roll_bp == pytest.approx(0.0)


def test_carry_roll_signs_and_uf():
    # upward-sloping curve, funding below yield: positive carry and roll
    cr = curves.carry_roll(5.0, 6.0, 5.9, funding_pct=4.5, h_years=0.25)
    d = curves.annuity_duration(6.0, 5.0)
    assert cr.carry_bp == pytest.approx(150 * 0.25 / d)
    assert cr.roll_bp == pytest.approx(10.0)
    real = curves.carry_roll(5.0, 2.4, 2.35, funding_pct=4.5, h_years=1 / 12, uf_accrual_pct=0.3)
    assert real.uf_bp == pytest.approx(30 / curves.annuity_duration(2.4, 5.0))
    assert real.total_bp == pytest.approx(real.carry_bp + real.roll_bp + real.uf_bp)
    assert curves.carry_roll(2.0, 5.0, None, 4.0, 0.25).total_bp is None


def test_interp_and_duration():
    assert curves.interp_linear([2, 5], [5.0, 5.6], 3.5) == pytest.approx(5.3)
    assert curves.interp_linear([2, 5], [5.0, 5.6], 1.75) is None  # no extrapolation
    assert curves.annuity_duration(0.0, 5.0) == pytest.approx(5.0)
    assert curves.annuity_duration(5.0, 0.5) == pytest.approx(0.5 / 1.025)
    assert 4.0 < curves.annuity_duration(5.0, 5.0) < 5.0


def test_avg_path_rate():
    steps = [(date(2026, 12, 17), 4.25)]
    # 30 days: 16 days at 4.50 (Dec 1-16), 14 at 4.25
    r = curves.avg_path_rate(4.5, steps, date(2026, 12, 1), date(2026, 12, 31))
    assert r == pytest.approx((4.5 * 16 + 4.25 * 14) / 30)
    assert curves.avg_path_rate(4.5, [], date(2026, 12, 1), date(2026, 12, 31), 5) == pytest.approx(
        4.55
    )
    # a step already effective before the start applies from the start
    assert (
        curves.avg_path_rate(4.5, [(date(2026, 11, 1), 4.0)], date(2026, 12, 1), date(2026, 12, 2))
        == 4.0
    )


def test_window_stats_and_percentiles():
    idx = pd.bdate_range("2026-01-01", "2026-11-13")
    s = pd.Series(np.linspace(1, 2, len(idx)), index=idx)
    st = curves.last_stats(s, "1Y")
    assert st["pct"] == pytest.approx(100.0) and st["z"] > 1.5
    assert curves.percentile_of(pd.Series([1.0, 2.0, 3.0, 4.0]), 2.0) == 50.0
    panel = pd.DataFrame({2.0: s, 5.0: s + 1})
    today = pd.DataFrame({"tenor": [2.0], "value": [1.0]})
    b = curves.box_stats(panel, date(2026, 11, 13), "6M", today)
    assert list(b["tenor"]) == [2.0, 5.0]
    row = b.set_index("tenor").loc[2.0]
    assert row["min"] <= row["p25"] <= row["median"] <= row["p75"] <= row["max"]
    assert row["pct"] == 0.0  # 1.0 is below every value in the last 6M
    assert np.isnan(b.set_index("tenor").loc[5.0, "pct"])
    with pytest.raises(ValueError):
        curves.window_start(date(2026, 1, 1), "5D")


def test_pca_level_slope_curvature():
    rng = np.random.default_rng(0)
    tenors = [1.0, 2.0, 5.0, 10.0]
    n = 300
    level = rng.normal(0, 0.05, n)
    slope_ = rng.normal(0, 0.02, n)
    base = np.array([5.0, 5.2, 5.5, 6.0])
    tw = np.array([-1.0, -0.5, 0.3, 1.0])
    chg = level[:, None] + slope_[:, None] * tw + rng.normal(0, 0.002, (n, 4))
    panel = pd.DataFrame(base + np.cumsum(chg, axis=0), columns=tenors,
                         index=pd.bdate_range("2025-01-01", periods=n))  # fmt: skip
    r = curves.pca_changes(panel, tenors)
    assert r is not None and r.explained.iloc[0] > 0.8
    assert (r.loadings["Level"] > 0).all()
    assert r.loadings["Slope"].iloc[-1] > r.loadings["Slope"].iloc[0]
    assert r.explained.sum() == pytest.approx(1.0, abs=0.01)
    assert len(r.scores) == n - 1
    assert curves.pca_changes(panel.iloc[:10], tenors) is None


def test_realized_vol():
    idx = pd.bdate_range("2026-08-01", "2026-11-13")
    s = pd.Series([5.0 + 0.01 * (i % 2) for i in range(len(idx))], index=idx)
    assert curves.realized_vol_bp(s, date(2026, 11, 13)) == pytest.approx(1.0, rel=0.05)
    assert curves.realized_vol_bp(s.iloc[:5], date(2026, 11, 13)) is None
