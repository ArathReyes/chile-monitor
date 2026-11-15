"""Monthly CPI nowcast table and implied 12-month paths (pure).

Scenarios per target month: seasonal average, last year's same month, EEE median, EOF median.
Market-implied monthly CPI is NOT computed: there are no public short-end UF swaps (the BDE UF
swap table starts at 1y and the Informativo Diario lists only nominal SPC 90/180/360d), and the
user chose not to model or interpolate it. The market view is shown separately as the BCCh's
published breakevens (12-month and longer rates).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

DISAGREE_BP = 10.0


def _ms(d: date) -> pd.Timestamp:
    return pd.Timestamp(d.year, d.month, 1)


def _clean(s: pd.Series) -> pd.Series:
    s = s.dropna().copy()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def survey_for_month(
    target: date, this_month: pd.Series, next_month: pd.Series, as_of: date
) -> tuple[float | None, str]:
    """Latest survey answer about `target` published by `as_of`.

    EEE convention (obs_date = survey month start): "en el mes" refers to the survey month,
    "mes siguiente" to the month after. Published ~10-12 days into the month, so a survey month
    counts only from its 12th day. Prefers the most recent survey that covers the target.
    """
    t = _ms(target)
    best: tuple[pd.Timestamp, float, str] | None = None
    for series, offset, label in ((this_month, 0, "en el mes"), (next_month, 1, "mes siguiente")):
        s = _clean(series)
        s = s[s.index + pd.Timedelta(days=11) <= pd.Timestamp(as_of)]
        survey_month = t - pd.DateOffset(months=offset)
        if survey_month in s.index and (best is None or survey_month > best[0]):
            best = (survey_month, float(s.loc[survey_month]), f"{label} ({survey_month:%b-%y})")
    return (best[1], best[2]) if best else (None, "")


def eof_for_month(
    target: date, eof_this: pd.Series, eof_next: pd.Series, eof_2m: pd.Series, as_of: date
) -> tuple[float | None, str]:
    """Latest EOF round (dated by survey date) answering for `target`: 'mes actual' when the
    round is in the target month, '1 mes adelante' the month before, '2 meses adelante' two
    months before."""
    t = _ms(target)
    cands = []
    for series, offset, label in (
        (eof_this, 0, "mes actual"),
        (eof_next, 1, "1 mes adelante"),
        (eof_2m, 2, "2 meses adelante"),
    ):
        s = _clean(series)
        s = s[s.index <= pd.Timestamp(as_of)]
        month = t - pd.DateOffset(months=offset)
        hit = s[(s.index >= month) & (s.index < month + pd.DateOffset(months=1))]
        if len(hit):
            cands.append((hit.index[-1], float(hit.iloc[-1]), f"{label} ({hit.index[-1]:%d-%b})"))
    if not cands:
        return None, ""
    d, v, lbl = max(cands)
    return v, lbl


def _seasonal(seasonal: pd.DataFrame, month: int) -> float | None:
    if month not in seasonal.index or pd.isna(seasonal.loc[month, "mean"]):
        return None
    return float(seasonal.loc[month, "mean"])


def nowcast_table(
    targets: list[date],
    mm_hist: pd.Series,
    seasonal: pd.DataFrame,
    eee_this: pd.Series,
    eee_next: pd.Series,
    eof_this: pd.Series,
    eof_next: pd.Series,
    eof_2m: pd.Series,
    as_of: date,
) -> pd.DataFrame:
    """One row per target month with each scenario's m/m CPI (pp) and a disagreement flag."""
    hist = _clean(mm_hist)
    rows = []
    for t in targets:
        ly = hist.get(_ms(t) - pd.DateOffset(years=1))
        eee, eee_lbl = survey_for_month(t, eee_this, eee_next, as_of)
        eof, eof_lbl = eof_for_month(t, eof_this, eof_next, eof_2m, as_of)
        row = {
            "month": _ms(t).date(),
            "seasonal_avg": _seasonal(seasonal, t.month),
            "last_year": None if ly is None else float(ly),
            "eee": eee,
            "eee_source": eee_lbl,
            "eof": eof,
            "eof_source": eof_lbl,
            "market": None,  # not available: no public short-end UF swaps (see module doc)
        }
        vals = [
            v
            for k, v in row.items()
            if k in ("seasonal_avg", "last_year", "eee", "eof") and v is not None
        ]
        row["spread_bp"] = (max(vals) - min(vals)) * 100 if len(vals) >= 2 else None
        row["disagree"] = bool(row["spread_bp"] is not None and row["spread_bp"] > DISAGREE_BP)
        rows.append(row)
    return pd.DataFrame(rows)


def implied_12m(
    mm_hist: pd.Series, scenario: dict[date, float], fill: dict[int, float], end_month: date
) -> float | None:
    """12-month CPI change ending at `end_month` (%): actual m/m where published, else the
    scenario's value for that month, else `fill[calendar_month]` (e.g. the seasonal mean).
    Returns None if any month has no value."""
    hist = _clean(mm_hist)
    total = 1.0
    for k in range(12):
        m = _ms(end_month) - pd.DateOffset(months=k)
        if m in hist.index:
            v = float(hist.loc[m])
        elif m.date() in scenario:
            v = scenario[m.date()]
        elif m.month in fill and fill[m.month] == fill[m.month]:
            v = fill[m.month]
        else:
            return None
        total *= 1 + v / 100
    return (total - 1) * 100


def next_months(last_published: date, n: int = 3) -> list[date]:
    start = _ms(last_published) + pd.DateOffset(months=1)
    return [(start + pd.DateOffset(months=i)).date() for i in range(n)]
