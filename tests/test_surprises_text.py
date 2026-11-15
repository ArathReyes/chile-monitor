from datetime import date

import pandas as pd
import pytest

from monitor.models.surprises import (
    decisions_from_meetings,
    decisions_from_tpm,
    summary_stats,
    surprise_table,
    survey_expectation,
    yoy,
)
from monitor.models.text_analysis import diff_html, diff_stats, word_diff


def daily(values: dict[str, float], end: str = "2026-03-10") -> pd.Series:
    idx = pd.date_range("2026-01-01", end, freq="D")
    s = pd.Series(index=idx, dtype=float)
    for d, v in values.items():
        s.loc[d:] = v
    return s


TPM = daily({"2026-01-01": 5.0, "2026-01-28": 4.75, "2026-03-04": 4.5})


def test_decisions_from_tpm():
    d = decisions_from_tpm(TPM)
    assert d["change_bp"].tolist() == [-25.0, -25.0]
    assert d["effective_date"].iloc[0] == date(2026, 1, 28)
    assert d["decision_date"].iloc[0] == date(2026, 1, 27)


def test_decisions_from_meetings_includes_hold():
    meetings = [
        (date(2026, 1, 27), date(2026, 1, 28), "m1"),
        (date(2026, 2, 10), date(2026, 2, 11), "m2"),
        (date(2026, 3, 3), date(2026, 3, 4), "m3"),
        (date(2026, 4, 1), date(2026, 4, 2), "future"),
    ]
    d = decisions_from_meetings(meetings, TPM, as_of=date(2026, 3, 10))
    assert d["change_bp"].tolist() == [-25.0, 0.0, -25.0]


def test_survey_expectation_prefers_recent_eof():
    eof = pd.Series([4.75, 4.5], index=pd.to_datetime(["2026-01-20", "2026-02-25"]))
    eee = pd.Series([4.75], index=pd.to_datetime(["2026-02-01"]))
    r, src, d = survey_expectation(date(2026, 3, 3), eof, eee, eee)
    assert (r, src, d) == (4.5, "EOF", date(2026, 2, 25))
    # no EOF round within 45 days -> EEE same-month horizon
    r, src, _ = survey_expectation(date(2026, 2, 20), eof.iloc[:0], eee, eee.iloc[:0])
    assert (r, src) == (4.75, "EEE")


def test_surprise_table_and_stats():
    d = decisions_from_tpm(TPM)
    eof = pd.Series([4.75, 4.75], index=pd.to_datetime(["2026-01-20", "2026-02-25"]))
    t = surprise_table(d, eof, pd.Series(dtype=float), pd.Series(dtype=float))
    assert t["expected_change_bp"].tolist() == [-25.0, 0.0]
    assert t["surprise_bp"].tolist() == [0.0, -25.0]
    st = summary_stats(t)
    assert st["hit_rate"] == 0.5 and st["avg_abs_surprise_bp"] == 12.5 and st["share_25"] == 1.0


def test_yoy():
    idx = pd.date_range("2025-01-01", periods=13, freq="MS")
    s = pd.Series([100.0] * 12 + [104.0], index=idx)
    y = yoy(s)
    assert y.iloc[-1] == pytest.approx(4.0)
    assert y.iloc[:12].isna().all()


def test_word_diff_and_html():
    old = "El Consejo decidió mantener la TPM en 4,5%."
    new = "El Consejo decidió aumentar la TPM a 4,75%."
    ops = word_diff(old, new)
    assert ("delete", "mantener") in ops and ("insert", "aumentar") in ops
    h = diff_html(old, new)
    assert "line-through" in h and "aumentar" in h
    assert diff_stats(old, old)["share_changed"] == 0
