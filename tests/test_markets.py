from datetime import date

import pandas as pd
import pytest

from monitor.models.prediction_markets import implied_probabilities, outcome_mid
from monitor.sources.kalshi import event_meeting_date, parse_candlesticks, parse_events
from monitor.sources.polymarket import outcome_bp, parse_event


def test_kalshi_parse_events(load_fixture):
    df = parse_events(load_fixture("kalshi_events_kxcbdchile.json")["events"])
    assert set(df["change_bp"]) == {-50, -25, 0, 25, 50}
    assert df["meeting_date"].iloc[0] == date(2026, 12, 15)
    assert df["yes_ask"].between(0, 1).all()


def test_kalshi_ticker_date_and_unknown_outcome():
    assert event_meeting_date("KXCBDCHILE-27JAN26") == date(2027, 1, 26)
    with pytest.raises(ValueError):
        parse_events([{"event_ticker": "KXCBDCHILE-26DEC15", "markets": [{"ticker": "X-FOO"}]}])


def test_kalshi_candles(load_fixture):
    c = parse_candlesticks(load_fixture("kalshi_candles_hold.json"))
    assert len(c) > 0 and c["yes_ask"].between(0, 1).all()


def test_polymarket_parse(load_fixture):
    df = parse_event(load_fixture("polymarket_event_bcch_jan2027.json")[0])
    assert df["meeting_date"].iloc[0] == date(2027, 1, 26)
    assert sorted(df["change_bp"]) == [-50, -25, 0, 25, 50]


@pytest.mark.parametrize(
    ("title", "bp"),
    [("25 bps decrease", -25), ("50+ bps increase", 50), ("No Change", 0), ("No change", 0)],
)
def test_polymarket_outcome_bp(title, bp):
    assert outcome_bp(title)[0] == bp


def test_polymarket_outcome_unknown():
    with pytest.raises(ValueError):
        outcome_bp("Something else")


def test_outcome_mid():
    assert outcome_mid(0.58, 0.62) == pytest.approx(0.60)
    assert outcome_mid(None, 0.02) == pytest.approx(0.01)
    assert outcome_mid(0.5, None) is None
    assert outcome_mid(0.7, 0.6) is None  # crossed


def test_implied_probabilities_normalize():
    df = pd.DataFrame(
        {
            "venue": "k", "event": "e", "meeting_date": date(2026, 12, 15),
            "outcome": ["hold", "hike"], "change_bp": [0, 25],
            "yes_bid": [0.58, 0.34], "yes_ask": [0.62, 0.38],
        }
    )  # fmt: skip
    out, summ = implied_probabilities(df)
    assert out["prob"].sum() == pytest.approx(1.0)
    assert summ["overround"].iloc[0] == pytest.approx(0.96)
    assert summ["expected_change_bp"].iloc[0] == pytest.approx(25 * 0.36 / 0.96)
