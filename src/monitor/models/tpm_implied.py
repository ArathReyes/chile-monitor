"""Market-implied TPM path from Cámara swaps (SPC). Pure functions, no I/O.

Model
-----
* The TPM is a step function that changes only on meeting effective dates.
* Overnight Cámara rate on business day d: r_d = TPM(d) + spread.
* The Cámara index compounds business-day rates over calendar days, ACT/360:
  growth(start, t) = prod_d (1 + r_d * n_d / 360), n_d = calendar days to the next business day.
* Swaps <= 1y: single payment at maturity, simple ACT/360:
  1 + R * T/360 = growth(start, maturity).
* Swaps > 1y: semiannual fixed coupons, ACT/360 (convention confirmed by the user, 2026-11-14),
  discounted on the same overnight curve: R = (1 - DF(T_n)) / sum_k tau_k DF(t_k).
* Quote bases: traded 90/180/360d are on a 360-day basis; the BDE tenor table (1y-10y) is on a
  365-day basis and is converted with R_360 = R_365 * 360/365 before fitting (assumption,
  documented in CLAUDE.md).

Fit
---
Unknowns x_1..x_N are post-meeting TPM levels. Minimize
  sum_s w_s (model_s - market_s)^2 + lam sum_i (x_i - prior_i)^2 + mu sum_i (x_i - x_{i-1})^2
  [+ sum_a w_a (x_a - anchor_a)^2 for prediction-market anchors]
with residuals in basis points (x_0 = current TPM). After the last step the TPM is held flat.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from scipy.optimize import least_squares

from monitor.cl_calendar import following, next_business_day

OUTCOMES_BP = (-50, -25, 0, 25, 50)


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class SwapQuote:
    name: str
    start: date  # first accrual date = trade date + 2 CL business days (cl_calendar.spc_start)
    rate: float  # percent, as quoted
    basis: int  # quote day-count basis: 360 or 365
    tenor_days: int | None = None  # single-payment swaps (<= 1y)
    tenor_years: int | None = None  # semiannual-coupon swaps (> 1y)
    weight: float = 1.0

    def __post_init__(self) -> None:
        if (self.tenor_days is None) == (self.tenor_years is None):
            raise ValueError(f"{self.name}: set exactly one of tenor_days / tenor_years")
        if self.basis not in (360, 365):
            raise ValueError(f"{self.name}: basis must be 360 or 365")

    @property
    def maturity(self) -> date:
        if self.tenor_days is not None:
            return following(self.start + timedelta(days=self.tenor_days))
        return following(self.start + relativedelta(years=self.tenor_years))

    @property
    def rate_360(self) -> float:
        """Quote converted to the model's ACT/360 basis."""
        return convert_basis(self.rate, self.basis, 360)


@dataclass(frozen=True)
class Step:
    """A possible TPM change: decision date, effective date, and whether it's a real meeting."""

    decision_date: date
    effective_date: date
    scheduled: bool = True
    label: str = ""


@dataclass(frozen=True)
class Anchor:
    """External expectation for the post-meeting TPM at one step (e.g. Kalshi)."""

    effective_date: date
    rate: float
    weight: float
    source: str = ""


@dataclass(frozen=True)
class FitConfig:
    lam: float = 0.005  # prior weight (per bp^2, relative to swap weight 1)
    mu: float = 0.25  # smoothness weight
    spread_bp: float = 0.0  # overnight Cámara - TPM spread
    lower: float = 0.0
    upper: float = 25.0


@dataclass
class PathFit:
    path: pd.DataFrame
    residuals: pd.DataFrame
    rmse_bp: float
    quality: str
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ conventions


def convert_basis(rate: float, from_basis: int, to_basis: int) -> float:
    """Convert a simple annual rate between ACT/360 and ACT/365 bases."""
    return rate * to_basis / from_basis


def monthly_grid_steps(after: date, n: int) -> list[Step]:
    """Unscheduled placeholder steps on the first business day of each month after `after`.

    Used only when real meeting dates are not configured; flagged scheduled=False.
    """
    steps, d = [], date(after.year, after.month, 1)
    while len(steps) < n:
        d = d + relativedelta(months=1)
        eff = following(d)
        steps.append(Step(eff, eff, scheduled=False, label=f"grid {eff:%b-%y}"))
    return steps


def build_steps(meetings: list[tuple[date, date, str]], curve_date: date, n: int) -> list[Step]:
    """Future meetings (decision, effective, label) after curve_date, padded with a grid to n."""
    future = sorted(
        (Step(dec, eff, True, label) for dec, eff, label in meetings if eff > curve_date),
        key=lambda s: s.effective_date,
    )[:n]
    if len(future) < n:
        last = future[-1].effective_date if future else curve_date
        future += monthly_grid_steps(last, n - len(future))
    return future


# --------------------------------------------------------------------- pricing


class DayGrid:
    """Business days in [start, end) with calendar-day accrual counts (built once per fit)."""

    def __init__(self, start: date, end: date) -> None:
        days: list[date] = []
        d = following(start)
        while d < end:
            days.append(d)
            d = next_business_day(d)
        if not days:
            raise ValueError("empty day grid")
        nxt = days[1:] + [next_business_day(days[-1])]
        self.days = days
        self.n = np.array([(b - a).days for a, b in zip(days, nxt, strict=True)], dtype=float)
        self.ord = np.array(days, dtype="datetime64[D]")

    def index(self, t: date) -> int:
        return int(np.searchsorted(self.ord, np.datetime64(t, "D"), side="left"))


class OvernightCurve:
    """Cumulative log-growth of the Cámara index over business days for one TPM path."""

    def __init__(
        self,
        grid: DayGrid,
        tpm0: float,
        effective_dates: list[date],
        levels: np.ndarray,
        spread_bp: float = 0.0,
    ) -> None:
        self.grid = grid
        idx = np.searchsorted(np.array(effective_dates, dtype="datetime64[D]"), grid.ord, "right")
        all_levels = np.concatenate([[tpm0], np.asarray(levels, dtype=float)])
        r = all_levels[idx] + spread_bp / 100.0
        self.cum = np.concatenate([[0.0], np.cumsum(np.log1p(r * grid.n / 36000.0))])

    def log_growth(self, t0: date, t1: date) -> float:
        """log growth of the index from t0 to t1 (both business days within the curve)."""
        return float(self.cum[self.grid.index(t1)] - self.cum[self.grid.index(t0)])

    def df(self, t0: date, t1: date) -> float:
        return math.exp(-self.log_growth(t0, t1))


def price_swap(q: SwapQuote, curve: OvernightCurve) -> float:
    """Model fair rate (percent, ACT/360) for a quote under the given overnight curve."""
    start = following(q.start)
    if q.tenor_days is not None:
        mat = q.maturity
        t = (mat - start).days
        return (math.exp(curve.log_growth(start, mat)) - 1.0) * 36000.0 / t
    dates = [
        following(start + relativedelta(months=6 * k)) for k in range(1, 2 * q.tenor_years + 1)
    ]
    prev, annuity = start, 0.0
    for d in dates:
        annuity += (d - prev).days / 360.0 * curve.df(start, d)
        prev = d
    return (1.0 - curve.df(start, dates[-1])) / annuity * 100.0


def grid_for(quotes: list[SwapQuote]) -> DayGrid:
    start = min(following(q.start) for q in quotes)
    return DayGrid(start, max(q.maturity for q in quotes) + timedelta(days=7))


def model_rates(
    quotes: list[SwapQuote],
    tpm0: float,
    steps: list[Step],
    levels: np.ndarray,
    spread_bp: float = 0.0,
    grid: DayGrid | None = None,
) -> np.ndarray:
    grid = grid or grid_for(quotes)
    curve = OvernightCurve(grid, tpm0, [s.effective_date for s in steps], levels, spread_bp)
    return np.array([price_swap(q, curve) for q in quotes])


# ------------------------------------------------------------------------- fit


def change_probabilities(change_bp: float) -> dict[int, float]:
    """FedWatch-style split of an expected change between the two nearest 25bp outcomes."""
    lo = math.floor(change_bp / 25.0) * 25
    p_hi = (change_bp - lo) / 25.0
    probs = {lo: 1.0 - p_hi, lo + 25: p_hi}
    return {k: v for k, v in probs.items() if v > 1e-9}


def _quality(rmse: float) -> str:
    return "good" if rmse < 2.0 else "fair" if rmse < 5.0 else "poor"


def fit_path(
    quotes: list[SwapQuote],
    tpm0: float,
    steps: list[Step],
    prior: np.ndarray | None = None,
    anchors: list[Anchor] | None = None,
    cfg: FitConfig | None = None,
) -> PathFit:
    cfg = cfg or FitConfig()
    n = len(steps)
    if n == 0 or not quotes:
        raise ValueError("need at least one step and one quote")
    prior_arr = np.full(n, tpm0) if prior is None else np.asarray(prior, dtype=float)
    market = np.array([q.rate_360 for q in quotes])
    w = np.sqrt([q.weight for q in quotes])
    eff = [s.effective_date for s in steps]
    grid = grid_for(quotes)
    anchor_rows = [
        (eff.index(a.effective_date), a) for a in (anchors or []) if a.effective_date in eff
    ]

    def residuals(x: np.ndarray) -> np.ndarray:
        model = model_rates(quotes, tpm0, steps, x, cfg.spread_bp, grid)
        parts = [w * (model - market) * 100.0]
        if cfg.lam > 0:
            parts.append(math.sqrt(cfg.lam) * (x - prior_arr) * 100.0)
        if cfg.mu > 0:
            parts.append(math.sqrt(cfg.mu) * np.diff(np.concatenate([[tpm0], x])) * 100.0)
        if anchor_rows:
            parts.append(
                np.array([math.sqrt(a.weight) * (x[i] - a.rate) * 100.0 for i, a in anchor_rows])
            )
        return np.concatenate(parts)

    x0 = np.clip(prior_arr, cfg.lower + 1e-6, cfg.upper - 1e-6)
    sol = least_squares(residuals, x0, bounds=(cfg.lower, cfg.upper), xtol=1e-12, ftol=1e-12)
    x = sol.x
    model = model_rates(quotes, tpm0, steps, x, cfg.spread_bp, grid)
    resid_bp = (model - market) * 100.0
    rmse = float(np.sqrt(np.mean(resid_bp**2)))

    prev = np.concatenate([[tpm0], x[:-1]])
    change = (x - prev) * 100.0
    path = pd.DataFrame(
        {
            "decision_date": [s.decision_date for s in steps],
            "effective_date": eff,
            "scheduled": [s.scheduled for s in steps],
            "label": [s.label for s in steps],
            "prior": prior_arr,
            "implied_tpm": x,
            "change_bp": change,
            "cum_change_bp": (x - tpm0) * 100.0,
        }
    )
    for bp in OUTCOMES_BP:
        path[f"p_{bp:+d}"] = [change_probabilities(c).get(bp, 0.0) for c in change]
    path["p_other"] = 1.0 - path[[f"p_{bp:+d}" for bp in OUTCOMES_BP]].sum(axis=1)

    residuals_df = pd.DataFrame(
        {
            "instrument": [q.name for q in quotes],
            "start": [q.start for q in quotes],
            "maturity": [q.maturity for q in quotes],
            "quote": [q.rate for q in quotes],
            "quote_basis": [q.basis for q in quotes],
            "market_act360": market,
            "model_act360": model,
            "resid_bp": resid_bp,
            "weight": [q.weight for q in quotes],
        }
    )
    notes = []
    informative = sum(1 for q in quotes if q.tenor_days is not None)
    if informative < n:
        notes.append(
            f"{informative} short-dated swaps for {n} unknown meeting levels: later meetings "
            "lean on the survey prior and smoothness terms."
        )
    if any(not s.scheduled for s in steps):
        notes.append("Some steps are an unscheduled monthly grid (meeting dates not configured).")
    return PathFit(path, residuals_df, rmse, _quality(rmse), notes)


# ----------------------------------------------------------------------- prior


def build_prior(
    steps: list[Step],
    tpm0: float,
    survey_date: date | None,
    meeting_points: dict[int, float] | None = None,
    horizon_points: dict[int, float] | None = None,
) -> np.ndarray:
    """Survey prior at each step, interpolated linearly in time.

    meeting_points: {k: rate} for the k-th scheduled future meeting after the survey (0 = next).
    horizon_points: {days_after_survey: rate} (e.g. EOF 12m -> 365, EEE 23m -> ~700).
    Falls back to a flat prior at tpm0 when no survey data is available.
    """
    if survey_date is None or not (meeting_points or horizon_points):
        return np.full(len(steps), tpm0)
    pts: list[tuple[int, float]] = [(0, tpm0)]
    scheduled = [s for s in steps if s.scheduled and s.decision_date > survey_date]
    for k, rate in (meeting_points or {}).items():
        if k < len(scheduled):
            pts.append(((scheduled[k].effective_date - survey_date).days, rate))
    for days, rate in (horizon_points or {}).items():
        pts.append((days, rate))
    pts.sort()
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts])
    t = np.array([(s.effective_date - survey_date).days for s in steps], dtype=float)
    return np.interp(t, xs, ys)


def average_spread_bp(tib: pd.Series, tpm: pd.Series, window: int = 20) -> float:
    """Recent average TIB - TPM in bp over the last `window` common observations."""
    both = pd.concat([tib, tpm], axis=1, join="inner").dropna().tail(window)
    if both.empty:
        return 0.0
    return float((both.iloc[:, 0] - both.iloc[:, 1]).mean() * 100.0)
