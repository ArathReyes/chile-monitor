"""Policy decisions and surprises vs survey / market expectations. Pure functions.

Decisions come from meeting dates when configured (includes holds); otherwise they are derived
from TPM changes in the BDE series (rate changes only; decision date = business day before the
effective date).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from monitor.cl_calendar import previous_business_day
from monitor.models.tpm_implied import FitConfig, Step, SwapQuote, fit_path

DECISION_COLUMNS = [
    "decision_date", "effective_date", "meeting_id", "tpm_before", "tpm_after", "change_bp",
    "from_meetings",
]  # fmt: skip
HIT_THRESHOLD_BP = 12.5
EOF_MAX_AGE_DAYS = 45  # EOF round must be within this window before the decision
EEE_PUBLICATION_LAG_DAYS = 12  # EEE dated 1st of month, published ~10th-12th


def _as_series(s: pd.Series) -> pd.Series:
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def _value_on_or_before(s: pd.Series, d: date) -> float | None:
    sub = s.loc[: pd.Timestamp(d)]
    return float(sub.iloc[-1]) if len(sub) else None


def decisions_from_tpm(tpm: pd.Series) -> pd.DataFrame:
    """Rate changes in the daily TPM series."""
    s = _as_series(tpm)
    if s.empty:
        return pd.DataFrame(columns=DECISION_COLUMNS)
    chg = s.diff()
    rows = []
    for ts, d in chg[chg.fillna(0) != 0].items():
        eff = ts.date()
        before = float(s.loc[:ts].iloc[-2])
        rows.append(
            {
                "decision_date": previous_business_day(eff),
                "effective_date": eff,
                "meeting_id": None,
                "tpm_before": before,
                "tpm_after": float(s.loc[ts]),
                "change_bp": round(d * 100, 2),
                "from_meetings": False,
            }
        )
    return pd.DataFrame(rows, columns=DECISION_COLUMNS)


def decisions_from_meetings(
    meetings: list[tuple[date, date, str]], tpm: pd.Series, as_of: date
) -> pd.DataFrame:
    """One row per past meeting (holds included). meetings: (decision, effective, id)."""
    s = _as_series(tpm)
    rows = []
    for dec, eff, mid in sorted(meetings):
        if eff > as_of:
            continue
        before = _value_on_or_before(s, dec)
        after = _value_on_or_before(s, eff)
        if before is None or after is None:
            continue
        rows.append(
            {
                "decision_date": dec,
                "effective_date": eff,
                "meeting_id": mid,
                "tpm_before": before,
                "tpm_after": after,
                "change_bp": round((after - before) * 100, 2),
                "from_meetings": True,
            }
        )
    return pd.DataFrame(rows, columns=DECISION_COLUMNS)


def survey_expectation(
    decision: date,
    eof_next: pd.Series,
    eee_this_month: pd.Series,
    eee_next_month: pd.Series,
) -> tuple[float | None, str, date | None]:
    """Latest survey TPM expectation for this meeting published before the decision.

    Primary: EOF 'próxima RPM' from a round within EOF_MAX_AGE_DAYS before the decision.
    Fallback: EEE 'en el mes' / 'mes siguiente' from a survey already published.
    """
    eof = _as_series(eof_next)
    sub = eof.loc[
        pd.Timestamp(decision - timedelta(days=EOF_MAX_AGE_DAYS)) : pd.Timestamp(
            decision - timedelta(days=1)
        )
    ]
    if len(sub):
        return float(sub.iloc[-1]), "EOF", sub.index[-1].date()
    cutoff = pd.Timestamp(decision - timedelta(days=EEE_PUBLICATION_LAG_DAYS))
    for series, offset in ((eee_this_month, 0), (eee_next_month, 1)):
        s = _as_series(series).loc[:cutoff]
        if not len(s):
            continue
        survey_month = s.index[-1]
        target = survey_month + pd.DateOffset(months=offset)
        if (target.year, target.month) == (decision.year, decision.month):
            return float(s.iloc[-1]), "EEE", survey_month.date()
    return None, "", None


def market_implied_change(
    decision: date, effective: date, tpm_before: float, spc90: pd.Series
) -> float | None:
    """Approximate SPC-90d-implied change at one meeting, from the quote the day before.

    Assumes a single move inside the 90-day window and no term/credit premium (approximation).
    """
    s = _as_series(spc90).loc[: pd.Timestamp(decision - timedelta(days=1))]
    if not len(s) or (decision - s.index[-1].date()).days > 7:
        return None
    q = SwapQuote("spc90", s.index[-1].date(), float(s.iloc[-1]), 360, tenor_days=90)
    fit = fit_path([q], tpm_before, [Step(decision, effective)], cfg=FitConfig(lam=0, mu=0))
    return float(fit.path["change_bp"].iloc[0])


def surprise_table(
    decisions: pd.DataFrame,
    eof_next: pd.Series,
    eee_this_month: pd.Series,
    eee_next_month: pd.Series,
    spc90: pd.Series | None = None,
) -> pd.DataFrame:
    out = decisions.copy()
    exp_rate, src, sdate, mkt = [], [], [], []
    for row in out.itertuples(index=False):
        r, s, d = survey_expectation(row.decision_date, eof_next, eee_this_month, eee_next_month)
        exp_rate.append(r)
        src.append(s)
        sdate.append(d)
        mkt.append(
            market_implied_change(row.decision_date, row.effective_date, row.tpm_before, spc90)
            if spc90 is not None
            else None
        )
    out["expected_tpm"] = exp_rate
    out["expected_source"] = src
    out["survey_date"] = sdate
    out["expected_change_bp"] = [
        None if r is None else round((r - b) * 100, 2)
        for r, b in zip(exp_rate, out["tpm_before"], strict=True)
    ]
    out["surprise_bp"] = out["change_bp"] - pd.to_numeric(out["expected_change_bp"])
    out["market_expected_bp"] = mkt
    out["market_surprise_bp"] = out["change_bp"] - pd.to_numeric(out["market_expected_bp"])
    return out


def summary_stats(table: pd.DataFrame) -> dict[str, float | int | None]:
    t = table.dropna(subset=["surprise_bp"])
    moves = table.loc[table["change_bp"] != 0, "change_bp"]
    hikes = moves[moves > 0]
    cuts = moves[moves < 0]
    absm = moves.abs()
    return {
        "n_decisions": int(len(table)),
        "n_with_expectation": int(len(t)),
        "hit_rate": float((t["surprise_bp"].abs() < HIT_THRESHOLD_BP).mean()) if len(t) else None,
        "avg_abs_surprise_bp": float(t["surprise_bp"].abs().mean()) if len(t) else None,
        "share_25": float((absm == 25).mean()) if len(moves) else None,
        "share_50": float((absm == 50).mean()) if len(moves) else None,
        "share_gt50": float((absm > 50).mean()) if len(moves) else None,
        "avg_hike_bp": float(hikes.mean()) if len(hikes) else None,
        "avg_cut_bp": float(cuts.mean()) if len(cuts) else None,
    }


def surprises_by_year(table: pd.DataFrame) -> pd.DataFrame:
    t = table.dropna(subset=["surprise_bp"]).copy()
    if t.empty:
        return pd.DataFrame(columns=["year", "n", "avg_abs_surprise_bp", "hit_rate"])
    t["year"] = pd.to_datetime(t["decision_date"]).dt.year
    g = t.groupby("year")["surprise_bp"]
    return pd.DataFrame(
        {
            "n": g.size(),
            "avg_abs_surprise_bp": g.apply(lambda x: x.abs().mean()),
            "hit_rate": g.apply(lambda x: (x.abs() < HIT_THRESHOLD_BP).mean()),
        }
    ).reset_index()


def yoy(index: pd.Series) -> pd.Series:
    """12-month % change of a monthly index (no filling)."""
    s = _as_series(index)
    return (s / s.shift(12, freq="MS").reindex(s.index) - 1) * 100
