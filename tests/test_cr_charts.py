"""Curve Snapshot builder and tenor axis (app/cr_charts.py), on fixture-like data."""

import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import cr_charts  # noqa: E402

TENORS = [0.25, 0.5, 1.0, 2.0, 5.0, 10.0]


@pytest.fixture
def panel():
    idx = pd.bdate_range("2025-06-02", "2026-11-13")
    rng = np.random.default_rng(1)
    base = np.array([4.7, 4.8, 4.9, 5.2, 5.5, 5.9])
    vals = base + np.cumsum(rng.normal(0, 0.02, (len(idx), len(TENORS))), axis=0)
    p = pd.DataFrame(vals, index=idx, columns=TENORS)
    p.loc["2026-11-13", 5.0] = np.nan  # today's 5Y didn't trade
    return p


def test_tenor_axis_positions_linear_and_sqrt():
    lin = cr_charts.tenor_axis(TENORS + [30.0], "linear")
    sq = cr_charts.tenor_axis(TENORS + [30.0], "sqrt")
    assert lin["ticktext"] == ["3M", "6M", "1Y", "2Y", "3Y", "5Y", "10Y", "20Y", "30Y"]
    assert lin["tickvals"] == [0.25, 0.5, 1, 2, 3, 5, 10, 20, 30]
    assert sq["tickvals"] == pytest.approx([math.sqrt(t) for t in lin["tickvals"]])
    assert sq["ticktext"] == lin["ticktext"]
    # true spacing: 2Y sits closer to 1Y than to 5Y on both scales
    for ax in (lin, sq):
        v = dict(zip(ax["ticktext"], ax["tickvals"], strict=True))
        assert v["2Y"] - v["1Y"] < v["5Y"] - v["2Y"]
    # ticks limited to the curve's range
    assert cr_charts.tenor_axis([2.0, 5.0, 10.0], "sqrt")["ticktext"] == ["2Y", "3Y", "5Y", "10Y"]


def test_box_widths_scale_with_local_spacing():
    xs = list(cr_charts.x_of(TENORS, "linear"))
    w = cr_charts.box_widths(xs)
    assert w[0] == pytest.approx(0.6 * 0.25) and w[-1] == pytest.approx(0.6 * 5)
    for i in range(len(xs) - 1):  # no overlap between neighbours
        assert xs[i] + w[i] / 2 <= xs[i + 1] - w[i + 1] / 2 + 1e-12


def test_values_mode_traces(panel):
    fig = cr_charts.snapshot_figure(
        panel, "pct", "Values", ["1D", "1W", "1M", "3M", "1Y"], "sqrt", "6M", date(2026, 11, 13)
    )
    boxes = [t for t in fig.data if t.type == "box"]
    lines = [t for t in fig.data if t.type == "scatter"]
    assert len(boxes) == len(TENORS)
    assert fig.data.index(boxes[-1]) < fig.data.index(lines[0])  # boxes drawn behind curves
    names = [t.name for t in lines]
    assert names[0] == "Today (13-Nov)" and names[1] == "1D (12-Nov)"
    assert len(lines) == 6
    today = lines[0]
    assert today.line.color == cr_charts.ORANGE and today.line.width == 3 and today.opacity == 1.0
    assert today.line.dash == "solid"
    # 5Y not observed today (and > 3bd? no: last obs 12-Nov is 1bd old) -> plotted from 12-Nov
    assert today.y[TENORS.index(5.0)] is not None
    dashes = [t.line.dash for t in lines[1:]]
    assert dashes == ["dash", "dot", "dashdot", "longdash", "longdashdot"]
    assert [t.opacity for t in lines[1:]] == [0.85, 0.70, 0.55, 0.40, 0.25]
    assert list(today.x) == pytest.approx([math.sqrt(t) for t in TENORS])
    assert boxes[0].upperfence[0] >= boxes[0].q3[0] >= boxes[0].median[0] >= boxes[0].q1[0]


def test_values_mode_breaks_line_at_missing_tenor(panel):
    p = panel.copy()
    p[5.0] = np.nan  # 5Y never traded: break, never interpolate
    fig = cr_charts.snapshot_figure(p, "pct", "Values", [], "linear", "6M", date(2026, 11, 13))
    today = [t for t in fig.data if t.type == "scatter"][0]
    assert today.y[TENORS.index(5.0)] is None and today.connectgaps is False


def test_deltas_mode_traces(panel):
    fig = cr_charts.snapshot_figure(
        panel, "pct", "Deltas", ["1D", "1W", "1M"], "sqrt", "6M", date(2026, 11, 13)
    )
    assert not [t for t in fig.data if t.type == "box"]  # boxes hidden in Deltas mode
    lines = [t for t in fig.data if t.type == "scatter"]
    assert [t.name for t in lines] == ["Δ 1D", "Δ 1W", "Δ 1M"]
    d1 = lines[0]
    assert d1.line.width == 3 and d1.line.dash == "solid" and d1.opacity == 1.0
    exp = (panel.loc["2026-11-13", 2.0] - panel.loc["2026-11-12", 2.0]) * 100
    assert d1.y[TENORS.index(2.0)] == pytest.approx(exp)
    assert fig.layout.yaxis.title.text == "Δ bp"
    assert any(s.type == "line" and s.y0 == 0 for s in fig.layout.shapes)  # zero line


def test_spread_units_and_empty_panel():
    p = pd.DataFrame({2.0: [300.0, 310.0]}, index=pd.to_datetime(["2026-11-12", "2026-11-13"]))
    fig = cr_charts.snapshot_figure(p, "bp", "Deltas", ["1D"], "sqrt", None, date(2026, 11, 13))
    assert [t for t in fig.data if t.type == "scatter"][0].y[0] == pytest.approx(10.0)
    empty = cr_charts.snapshot_figure(pd.DataFrame(), "pct", "Values", ["1D"], "sqrt", "6M")
    assert len(empty.data) == 0
