import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

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
