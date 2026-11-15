"""Out-of-sample backtest of the market-implied TPM path. Pure functions, no I/O.

For each past BCCh meeting and horizon h (CL business days before the decision), the implied
path is refit using only observations dated <= the as-of date and compared with the realized
TPM change, alongside three benchmarks: the EOF survey, a zero-change random walk, and the
single-step SPC-90d approximation.

Information-set rules (no look-ahead)
-------------------------------------
* SPC 90/180/360d: latest quote dated <= as-of (a BDE quote dated d is the close of trade date d;
  a trader at the as-of close has it), skipped if older than QUOTE_MAX_AGE_DAYS.
* EOF: a round dated d is used only from d + EOF_LAG_DAYS (results come out after the survey).
* EEE: dated the 1st of the month, used only EEE_PUBLICATION_LAG_DAYS later (as in surprises.py).
* TPM / TIB: observations dated <= as-of.
* Meeting dates: the BCCh publishes its calendar in advance, so true dates are legitimate steps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta
from typing import NamedTuple

import numpy as np
import pandas as pd

from monitor.cl_calendar import previous_business_day, spc_start
from monitor.models.surprises import EEE_PUBLICATION_LAG_DAYS, EOF_MAX_AGE_DAYS
from monitor.models.tpm_implied import (
    EOF_KEYS,
    EOF_MEETING_KEYS,
    EofRound,
    FitConfig,
    Meeting,
    Step,
    SwapQuote,
    average_spread_bp,
    build_steps,
    eof_prior,
    fit_path,
    latest_eof_round,
    meetings_between,
)

N_STEPS = 8
SHORT_SWAPS = (("cl_spc_nom_90d", 90), ("cl_spc_nom_180d", 180), ("cl_spc_nom_360d", 360))
QUOTE_MAX_AGE_DAYS = 7
EOF_LAG_DAYS = 1
DEAD_BAND_BP = 5.0
METHODS = ("model", "eof", "rw", "spc90")
TARGETS = ("m1", "cum2", "cum3")
REGIMES = (
    ("hiking", date(2021, 5, 1), date(2022, 10, 31)),
    ("plateau", date(2022, 11, 1), date(2023, 6, 30)),
    ("cutting", date(2023, 7, 1), date(2025, 12, 31)),
    ("2026", date(2026, 1, 1), date(2026, 12, 31)),
)


class FitInputs(NamedTuple):
    quotes: list[SwapQuote]
    tpm0: float
    steps: list[Step]
    prior: np.ndarray
    spread_bp: float


# ------------------------------------------------------------------- helpers


def _upto(s: pd.Series, t: date) -> pd.Series:
    """Observations dated <= t (sorted, NaN dropped)."""
    s = s.dropna().sort_index()
    return s.loc[: pd.Timestamp(t)]


def _last(s: pd.Series, t: date) -> tuple[date, float] | None:
    sub = _upto(s, t)
    return (sub.index[-1].date(), float(sub.iloc[-1])) if len(sub) else None


def _get(series: dict[str, pd.Series], sid: str) -> pd.Series:
    return series.get(sid, pd.Series(dtype=float))


def subtract_business_days(d: date, n: int) -> date:
    """d minus n Chilean business days (n >= 1)."""
    for _ in range(n):
        d = previous_business_day(d)
    return d


def regime_of(d: date) -> str:
    for name, lo, hi in REGIMES:
        if lo <= d <= hi:
            return name
    return "other"


# -------------------------------------------------------------------- inputs


def quotes_as_of(t: date, series: dict[str, pd.Series]) -> list[SwapQuote]:
    """Latest 90/180/360d SPC quotes dated <= t and within QUOTE_MAX_AGE_DAYS of t."""
    out = []
    for sid, days in SHORT_SWAPS:
        last = _last(_get(series, sid), t)
        if last is None or (t - last[0]).days > QUOTE_MAX_AGE_DAYS:
            continue
        out.append(SwapQuote(sid, spc_start(last[0]), last[1], 360, tenor_days=days))
    return out


def eof_round_as_of(t: date, series: dict[str, pd.Series]) -> EofRound | None:
    """Latest EOF round usable at t (survey date + EOF_LAG_DAYS <= t); values from it only."""
    eof = {k: series[f"eof_tpm_{k}"] for k in EOF_KEYS if f"eof_tpm_{k}" in series}
    return latest_eof_round(eof, t - timedelta(days=EOF_LAG_DAYS))


def inputs_as_of(
    t: date,
    series: dict[str, pd.Series],
    meetings: Sequence[Meeting],
    align_prior: bool = True,
) -> FitInputs:
    """Fit inputs using only observations dated <= t (see module docstring)."""
    tpm = _upto(_get(series, "cl_tpm"), t)
    if tpm.empty:
        raise ValueError(f"no TPM observation on or before {t}")
    tpm0 = float(tpm.iloc[-1])
    quotes = quotes_as_of(t, series)
    steps = build_steps(list(meetings), t, N_STEPS)
    prior = eof_prior(steps, tpm0, eof_round_as_of(t, series), meetings, align_prior)
    spread = average_spread_bp(_upto(_get(series, "cl_tib"), t), tpm)
    return FitInputs(quotes, tpm0, steps, prior, spread)


# ---------------------------------------------------------------- benchmarks


def eof_expectation(
    t: date,
    target: int,
    series: dict[str, pd.Series],
    meetings: Sequence[Meeting],
) -> tuple[list[float | None], str]:
    """Survey TPM for meetings target, target+1, target+2 (index into sorted meetings).

    Mirrors surprises.survey_expectation with t as the cutoff: EOF round within
    EOF_MAX_AGE_DAYS of the decision (position-aware: meetings held since the round are skipped),
    else EEE this/next month published by t (meeting 1 only).
    """
    ms = sorted(meetings)
    dec = ms[target][0]
    rnd = eof_round_as_of(t, series)
    if rnd is not None and (dec - rnd.survey_date).days <= EOF_MAX_AGE_DAYS:
        k = meetings_between(ms, rnd.survey_date, dec)
        out = []
        for j in range(3):
            key = EOF_MEETING_KEYS[k + j] if k + j < len(EOF_MEETING_KEYS) else None
            out.append(rnd.values.get(key) if key else None)
        if out[0] is not None:
            return out, "EOF"
    return [eee_expectation(t, dec, series), None, None], "EEE"


def eee_expectation(t: date, decision: date, series: dict[str, pd.Series]) -> float | None:
    cutoff = t - timedelta(days=EEE_PUBLICATION_LAG_DAYS)
    for sid, offset in (("eee_tpm_this_month", 0), ("eee_tpm_next_month", 1)):
        last = _last(_get(series, sid), cutoff)
        if last is None:
            continue
        target = pd.Timestamp(last[0]) + pd.DateOffset(months=offset)
        if (target.year, target.month) == (decision.year, decision.month):
            return last[1]
    return None


def spc90_single_step(
    t: date, step: Step, tpm0: float, series: dict[str, pd.Series]
) -> float | None:
    """surprises.market_implied_change with the as-of quote (single move, no spread)."""
    q = [q for q in quotes_as_of(t, series) if q.name == "cl_spc_nom_90d"]
    if not q:
        return None
    fit = fit_path(q, tpm0, [step], cfg=FitConfig(lam=0, mu=0))
    return float(fit.path["change_bp"].iloc[0])


# ------------------------------------------------------------------ backtest


def _tpm_on(tpm: pd.Series, d: date) -> float | None:
    last = _last(tpm, d)
    return last[1] if last else None


def _bp(a: float | None, b: float | None) -> float:
    return np.nan if a is None or b is None else round((a - b) * 100.0, 4)


def _realized(tpm: pd.Series, ms: list[Meeting], i: int, last_obs: date) -> list[float | None]:
    """TPM after meetings i, i+1, i+2 (None when not yet effective in the data)."""
    out = []
    for j in range(i, i + 3):
        ok = j < len(ms) and ms[j][1] <= last_obs
        out.append(_tpm_on(tpm, ms[j][1]) if ok else None)
    return out


def backtest_row(
    i: int,
    h: int,
    series: dict[str, pd.Series],
    ms: list[Meeting],
    cfg: FitConfig,
    align_prior: bool = True,
) -> dict:
    tpm = _get(series, "cl_tpm").dropna().sort_index()
    dec, eff, mid = ms[i]
    t = subtract_business_days(dec, h)
    row: dict = {"meeting_id": mid, "decision_date": dec, "effective_date": eff, "horizon_bd": h}
    row.update({"as_of": t, "regime": regime_of(dec), "skip_reason": ""})
    tpm_dec = _tpm_on(tpm, dec)
    after = _realized(tpm, ms, i, tpm.index[-1].date())
    row["actual_change_bp"] = _bp(after[0], tpm_dec)
    inp = inputs_as_of(t, series, ms, align_prior)
    row.update({"tpm0": inp.tpm0, "spread_bp": inp.spread_bp, "n_quotes": len(inp.quotes)})
    row["actual_cum2_bp"] = _bp(after[1], inp.tpm0)
    row["actual_cum3_bp"] = _bp(after[2], inp.tpm0)
    row["actual_m1_vs_tpm0_bp"] = _bp(after[0], inp.tpm0)
    rnd = eof_round_as_of(t, series)
    row["eof_round"] = rnd.survey_date if rnd else None
    row["prior_offset"] = (
        meetings_between(ms, rnd.survey_date, inp.steps[0].decision_date) if rnd else 0
    )
    row.update(_benchmarks(t, i, inp, series, ms))
    row.update(_model(inp, ms[i], cfg))
    return row


def _benchmarks(t: date, i: int, inp: FitInputs, series: dict, ms: list[Meeting]) -> dict:
    exp, src = eof_expectation(t, i, series, ms)
    out = {"eof_source": src if exp[0] is not None else ""}
    out["eof_m1_bp"] = _bp(exp[0], inp.tpm0)
    out["eof_cum2_bp"] = _bp(exp[1], inp.tpm0)
    out["eof_cum3_bp"] = _bp(exp[2], inp.tpm0)
    for k in TARGETS:
        out[f"rw_{k}_bp"] = 0.0
    step = Step(ms[i][0], ms[i][1])
    s90 = spc90_single_step(t, step, inp.tpm0, series)
    out["spc90_m1_bp"] = np.nan if s90 is None else s90
    out["spc90_cum2_bp"] = np.nan
    out["spc90_cum3_bp"] = np.nan
    return out


def _model(inp: FitInputs, target: Meeting, cfg: FitConfig) -> dict:
    nan = {f"model_{k}_bp": np.nan for k in TARGETS}
    if not inp.quotes:
        return {**nan, "skip_reason": "no SPC quote within 7 days"}
    if inp.steps[0].decision_date != target[0]:
        return {**nan, "skip_reason": f"target is not steps[0] ({inp.steps[0].decision_date})"}
    fit = fit_path(
        inp.quotes, inp.tpm0, inp.steps, inp.prior, None, replace(cfg, spread_bp=inp.spread_bp)
    )
    p = fit.path
    return {
        "model_m1_bp": float(p["change_bp"].iloc[0]),
        "model_cum2_bp": float(p["cum_change_bp"].iloc[1]),
        "model_cum3_bp": float(p["cum_change_bp"].iloc[2]),
        "fit_rmse_bp": fit.rmse_bp,
        "prior_m1_bp": float((inp.prior[0] - inp.tpm0) * 100.0),
    }


def evaluable_meetings(meetings: Sequence[Meeting], tpm: pd.Series) -> list[int]:
    """Indices (into sorted meetings) whose effective date is covered by the TPM data."""
    last = tpm.dropna().index.max().date()
    return [i for i, m in enumerate(sorted(meetings)) if m[1] <= last]


def run_backtest(
    series: dict[str, pd.Series],
    meetings: Sequence[Meeting],
    horizons_bd: Sequence[int] = (1, 5, 20),
    cfg: FitConfig | None = None,
    align_prior: bool = True,
    targets: Sequence[int] | None = None,
) -> pd.DataFrame:
    """One row per (meeting, horizon). targets: meeting indices to evaluate (default: all)."""
    cfg = cfg or FitConfig()
    ms = sorted(meetings)
    idx = evaluable_meetings(ms, _get(series, "cl_tpm")) if targets is None else list(targets)
    rows = [backtest_row(i, h, series, ms, cfg, align_prior) for i in idx for h in horizons_bd]
    return pd.DataFrame(rows)


# ------------------------------------------------------------------- metrics


def _sign(x: pd.Series) -> pd.Series:
    return np.sign(x.where(x.abs() > DEAD_BAND_BP, 0.0))


def metrics(pred: pd.Series, actual: pd.Series) -> dict[str, float]:
    ok = pred.notna() & actual.notna()
    p, a = pred[ok].astype(float), actual[ok].astype(float)
    if not len(p):
        return {"n": 0, "mae_bp": np.nan, "rmse_bp": np.nan, "bias_bp": np.nan,
                "dir_hit": np.nan, "bucket_hit": np.nan}  # fmt: skip
    err = p - a
    return {
        "n": int(len(p)),
        "mae_bp": float(err.abs().mean()),
        "rmse_bp": float(np.sqrt((err**2).mean())),
        "bias_bp": float(err.mean()),
        "dir_hit": float((_sign(p) == _sign(a)).mean()),
        "bucket_hit": float(((p / 25.0).round() * 25.0 == a.round(2)).mean()),
    }


def _actual_col(target: str) -> str:
    return "actual_change_bp" if target == "m1" else f"actual_{target}_bp"


def summarize(df: pd.DataFrame, by_regime: bool = True) -> pd.DataFrame:
    """Metrics per target, method, horizon (and regime, with 'all' rows)."""
    rows = []
    groups = [("all", df)]
    if by_regime:
        groups += [(r, g) for r, g in df.groupby("regime", sort=False)]
    for regime, g in groups:
        for target, method in _combos(df):
            for hz, gh in g.groupby("horizon_bd"):
                m = metrics(gh[f"{method}_{target}_bp"], gh[_actual_col(target)])
                if m["n"]:
                    rows.append({"target": target, "method": method, "horizon_bd": hz,
                                 "regime": regime, **m})  # fmt: skip
    return pd.DataFrame(rows)


def _combos(df: pd.DataFrame) -> list[tuple[str, str]]:
    return [(t, m) for t in TARGETS for m in METHODS if df.get(f"{m}_{t}_bp") is not None]


def sensitivity_grid(
    series: dict[str, pd.Series],
    meetings: Sequence[Meeting],
    lams: Sequence[float] = (0.0, 0.005, 0.02, 0.1),
    mus: Sequence[float] = (0.05, 0.25, 1.0),
    horizon_bd: int = 1,
) -> pd.DataFrame:
    """MAE / RMSE of the model for meeting 1 and cumulative-to-meeting-3 over a λ/μ grid."""
    rows = []
    for lam in lams:
        for mu in mus:
            df = run_backtest(series, meetings, (horizon_bd,), FitConfig(lam=lam, mu=mu))
            m1 = metrics(df["model_m1_bp"], df["actual_change_bp"])
            c3 = metrics(df["model_cum3_bp"], df["actual_cum3_bp"])
            rows.append({"lam": lam, "mu": mu, "n_m1": m1["n"], "mae_m1": m1["mae_bp"],
                         "rmse_m1": m1["rmse_bp"], "bucket_m1": m1["bucket_hit"],
                         "n_cum3": c3["n"], "mae_cum3": c3["mae_bp"],
                         "rmse_cum3": c3["rmse_bp"]})  # fmt: skip
    return pd.DataFrame(rows)
