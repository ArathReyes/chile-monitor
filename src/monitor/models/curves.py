"""Curves & Rates analytics (spec 03). Pure functions, no I/O.

Conventions
-----------
* **One rate convention:** every curve point is a % annual rate on a **365-day basis**. Swap quotes
  on a 360-day basis (traded SPC 90/180/360d) are converted with R_365 = R_360 x 365/360
  (`tpm_implied.convert_basis`). BDE bond yields are already "base 365" (compounding not stated
  by the BDE: VERIFY).
* **As-of rule:** comparisons use the data's own `obs_date`, never the fetch date. `value_asof`
  returns the last observation on or before a target date together with that date.
* **Staleness:** a point is *stale* when its last observation is more than `STALE_BD` Chilean
  business days older than the reference date (hollow marker), and *hidden* beyond `MAX_AGE_BD`
  (e.g. the 1y bond benchmarks, which stopped trading in Dec 2025).
* **Spreads** (bp) are computed only where both legs are observed on the same obs_date and tenor;
  a missing leg is never filled. "Swap spread (Cámara − BTP)" is not an asset-swap spread.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from scipy.optimize import least_squares

from monitor.cl_calendar import business_days, previous_business_day
from monitor.models.tpm_implied import convert_basis

TARGET_BASIS = 365
STALE_BD = 3
MAX_AGE_BD = 20


@dataclass(frozen=True)
class CurveDef:
    key: str
    label: str
    unit: str  # "pct" (outright curves) or "bp" (spreads)
    legs: tuple[str, str] | None = None  # spreads: (long leg, short leg), value = a - b
    fit: bool = False  # NSS fitted daily (swap curves only: bonds have too few live tenors)


CURVES: dict[str, CurveDef] = {
    c.key: c
    for c in (
        CurveDef("nom_bond", "Nominal bonds", "pct"),
        CurveDef("nom_swap", "Nominal swaps", "pct", fit=True),
        CurveDef("real_bond", "Real bonds", "pct"),
        CurveDef("real_swap", "Real swaps", "pct", fit=True),
        CurveDef("swap_bei", "Swap breakeven", "bp", ("nom_swap", "real_swap")),
        CurveDef("bond_bei", "Bond breakeven", "bp", ("nom_bond", "real_bond")),
        CurveDef("swap_spread", "Swap spread (Cámara − BTP)", "bp", ("nom_swap", "nom_bond")),
    )
}
OUTRIGHT = [k for k, c in CURVES.items() if c.legs is None]
SPREADS = [k for k, c in CURVES.items() if c.legs is not None]

TENOR_LABELS = {0.25: "3M", 0.5: "6M", 1.0: "1Y", 2.0: "2Y", 3.0: "3Y", 4.0: "4Y", 5.0: "5Y",
                7.0: "7Y", 10.0: "10Y", 20.0: "20Y", 30.0: "30Y"}  # fmt: skip


def tenor_label(t: float) -> str:
    if float(t) in TENOR_LABELS:
        return TENOR_LABELS[float(t)]
    return f"{t * 12:g}M" if t < 1 else f"{t:g}Y"


# ---------------------------------------------------------------- curve panels


@dataclass(frozen=True)
class CurvePoint:
    series_id: str
    tenor: float
    basis: int = TARGET_BASIS


def to_365(rate: float | pd.Series, basis: int) -> float | pd.Series:
    return rate if basis == TARGET_BASIS else convert_basis(rate, basis, TARGET_BASIS)


PLACEHOLDER_GAP = 0.5  # pp


def zero_placeholders(s: pd.Series, gap: float = PLACEHOLDER_GAP) -> pd.DatetimeIndex:
    """Dates of exact-zero prints that sit > `gap` pp from both neighbours on the same side
    (e.g. BDE SPC UF 20Y = 0.000000 on 2026-10-21 between 2.39 and 2.32). Genuine near-zero
    rates (real yields in 2019-21) are kept."""
    s = s.dropna()
    prev, nxt = s.shift(1), s.shift(-1)
    bad = (s == 0) & (((prev > gap) & (nxt > gap)) | ((prev < -gap) & (nxt < -gap)))
    return pd.DatetimeIndex(s.index[bad.to_numpy()])


def drop_placeholders(s: pd.Series) -> pd.Series:
    """Remove zero placeholders (dropped, never replaced)."""
    s = s.dropna()
    return s.drop(zero_placeholders(s))


def curve_panel(wide: pd.DataFrame, points: Sequence[CurvePoint]) -> pd.DataFrame:
    """obs_date x tenor_years panel of rates on the 365 basis; NaN where a tenor didn't trade.
    Zero placeholders are dropped (see zero_placeholders)."""
    cols = {}
    for p in sorted(points, key=lambda p: p.tenor):
        s = wide[p.series_id] if p.series_id in wide.columns else pd.Series(dtype=float)
        cols[p.tenor] = to_365(drop_placeholders(s.astype(float)), p.basis)
    out = pd.DataFrame(cols)
    out.index = pd.DatetimeIndex(out.index, name="obs_date")
    out.columns.name = "tenor"
    return out.dropna(how="all").sort_index()


def spread_panel(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """(a - b) in bp on tenors present in both panels, only where both legs share an obs_date."""
    tenors = [t for t in a.columns if t in b.columns]
    idx = a.index.intersection(b.index)
    out = (a.loc[idx, tenors] - b.loc[idx, tenors]) * 100.0
    out.columns.name = "tenor"
    return out.dropna(how="all")


def spread_series(a: pd.Series, b: pd.Series) -> pd.Series:
    """(a - b) in bp on dates where both are observed. Inputs in % on the same basis."""
    both = pd.concat([a, b], axis=1, join="inner").dropna()
    return (both.iloc[:, 0] - both.iloc[:, 1]) * 100.0


# --------------------------------------------------------------------- as-of


@dataclass(frozen=True)
class AsOf:
    value: float
    obs_date: date


def value_asof(s: pd.Series, target: date) -> AsOf | None:
    """Last non-missing observation on or before `target`, with its actual date."""
    s = s.dropna()
    s = s.loc[: pd.Timestamp(target)]
    if s.empty:
        return None
    return AsOf(float(s.iloc[-1]), s.index[-1].date())


def bd_age(obs_date: date, ref: date) -> int:
    """Chilean business days in (obs_date, ref]: 0 when observed on the reference day."""
    if obs_date >= ref:
        return 0
    return len(business_days(obs_date + timedelta(days=1), ref + timedelta(days=1)))


def curve_asof(
    panel: pd.DataFrame,
    target: date,
    stale_bd: int = STALE_BD,
    max_age_bd: int | None = MAX_AGE_BD,
) -> pd.DataFrame:
    """Per tenor: value as of `target`, its obs_date, age (business days) and stale flag.

    Tenors with no observation, or older than `max_age_bd`, are left out (never interpolated).
    """
    rows = []
    for t in panel.columns:
        a = value_asof(panel[t], target)
        if a is None:
            continue
        age = bd_age(a.obs_date, target)
        if max_age_bd is not None and age > max_age_bd:
            continue
        rows.append((float(t), a.value, a.obs_date, age, age > stale_bd))
    return pd.DataFrame(rows, columns=["tenor", "value", "obs_date", "age_bd", "stale"])


HORIZONS = ("1D", "1W", "1M", "3M", "1Y")


def horizon_target(ref: date, h: str) -> date:
    """Target date for a look-back horizon from the reference (latest) date."""
    if h == "1D":
        return previous_business_day(ref)
    if h == "1W":
        return ref - timedelta(days=7)
    months = {"1M": 1, "3M": 3, "1Y": 12}.get(h)
    if months is None:
        raise ValueError(f"unknown horizon {h}")
    return ref - relativedelta(months=months)


def latest_date(panel: pd.DataFrame) -> date | None:
    p = panel.dropna(how="all")
    return None if p.empty else p.index[-1].date()


def tenors_of(points: Iterable[CurvePoint]) -> list[float]:
    return sorted({p.tenor for p in points})


def points_from_specs(specs: Iterable, curve: str) -> list[CurvePoint]:
    """CurvePoints of one outright curve from SeriesSpec-like objects."""
    return sorted(
        (
            CurvePoint(s.series_id, float(s.tenor_years), s.basis or TARGET_BASIS)
            for s in specs
            if s.curve == curve and not s.derived and s.tenor_years is not None
        ),
        key=lambda p: p.tenor,
    )


def derived_legs(source_code: str) -> tuple[str, str]:
    """Parse a derived spread's source_code "leg_a - leg_b"."""
    parts = [p.strip() for p in source_code.split(" - ")]
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"derived source_code must be 'a - b', got {source_code!r}")
    return parts[0], parts[1]


def compute_derived(
    wide: pd.DataFrame, derived_specs: Iterable, bases: dict[str, int]
) -> pd.DataFrame:
    """Tidy [series_id, obs_date, value] for derived spreads (bp), same-date legs only."""
    frames = []
    for s in derived_specs:
        a, b = derived_legs(s.source_code)
        if a not in wide.columns or b not in wide.columns:
            continue
        sa = to_365(drop_placeholders(wide[a]), bases.get(a, TARGET_BASIS))
        sb = to_365(drop_placeholders(wide[b]), bases.get(b, TARGET_BASIS))
        sp = spread_series(sa, sb)
        if sp.empty:
            continue
        frames.append(pd.DataFrame({"series_id": s.series_id, "obs_date": sp.index, "value": sp}))
    if not frames:
        return pd.DataFrame(columns=["series_id", "obs_date", "value"])
    return pd.concat(frames, ignore_index=True)


# ====================================================================== Phase 2
# ------------------------------------------------------------ Nelson-Siegel-Svensson
#
# Svensson (1994), NBER WP 4871:
#   y(t) = b0 + b1 L1(t/tau1) + b2 [L1(t/tau1) - e^(-t/tau1)] + b3 [L1(t/tau2) - e^(-t/tau2)],
#   L1(x) = (1 - e^-x) / x.
# Fitted to the observed (par) quotes of one curve on one day, treated as zero rates for forwards
# and rolldown (approximation: no bootstrapping). Bounds keep tau1 (short hump) below tau2 (long
# hump) so the two curvature terms stay identifiable; a small ridge on b2/b3 stabilizes days with
# few points. Initial guess: previous day's fit, else a (tau1, tau2) grid with linear betas.

NSS_MIN_POINTS = 4
NSS_RIDGE = 1e-5  # weight on b2, b3; 1e-5 = sub-bp median RMSE with stable 5y5y (2025-26 test)
NSS_LOWER = np.array([-10.0, -25.0, -40.0, -40.0, 0.1, 4.0])
NSS_UPPER = np.array([30.0, 25.0, 40.0, 40.0, 4.0, 30.0])
NSS_NAMES = ("b0", "b1", "b2", "b3", "tau1", "tau2")
WARM_OK_BP = 1.5  # accept a warm-started fit below this RMSE without re-running the grid


@dataclass(frozen=True)
class NssFit:
    params: dict[str, float] | None
    rmse_bp: float
    residuals_bp: dict[float, float]
    n_points: int
    note: str | None = None

    @property
    def ok(self) -> bool:
        return self.params is not None

    @property
    def quality(self) -> str:
        if not self.ok:
            return "skipped"
        return "good" if self.rmse_bp < 3 else "fair" if self.rmse_bp < 8 else "poor"


def _l1(x: np.ndarray) -> np.ndarray:
    return np.where(x < 1e-8, 1.0 - x / 2, (1.0 - np.exp(-x)) / np.where(x < 1e-8, 1.0, x))


def nss_loadings(t: np.ndarray, tau1: float, tau2: float) -> np.ndarray:
    t = np.asarray(t, dtype=float)
    x1, x2 = t / tau1, t / tau2
    l1, l2 = _l1(x1), _l1(x2)
    return np.column_stack([np.ones_like(t), l1, l1 - np.exp(-x1), l2 - np.exp(-x2)])


def nss_yield(t: np.ndarray | float, params: dict[str, float]) -> np.ndarray:
    b = np.array([params[k] for k in NSS_NAMES[:4]])
    out = nss_loadings(np.atleast_1d(t), params["tau1"], params["tau2"]) @ b
    return out


def _grid_starts(t: np.ndarray, y: np.ndarray, k: int = 3) -> list[np.ndarray]:
    """Best k (tau1, tau2) grid points with betas from ridge least squares (same objective as
    the refinement, in % units)."""
    cands = []
    ridge = np.sqrt(NSS_RIDGE) * np.eye(4)[2:]
    for tau1 in np.geomspace(0.15, 3.9, 12):
        for tau2 in np.geomspace(4.1, 28.0, 10):
            x = nss_loadings(t, tau1, tau2)
            beta, *_ = np.linalg.lstsq(
                np.vstack([x, ridge]), np.concatenate([y, [0, 0]]), rcond=None
            )
            sse = float(np.sum((x @ beta - y) ** 2) + np.sum((ridge @ beta) ** 2))
            cands.append((sse, np.concatenate([beta, [tau1, tau2]])))
    cands.sort(key=lambda c: c[0])
    return [c[1] for c in cands[:k]]


def fit_nss(
    tenors: Sequence[float],
    yields: Sequence[float],
    prev: dict[str, float] | None = None,
    min_points: int = NSS_MIN_POINTS,
) -> NssFit:
    """Fit NSS to one day's observed points (tenor years, % yields). Skips with a reason."""
    t = np.asarray(tenors, dtype=float)
    y = np.asarray(yields, dtype=float)
    keep = ~np.isnan(y)
    t, y = t[keep], y[keep]
    n = len(t)
    if n < min_points:
        return NssFit(None, float("nan"), {}, n, f"{n} points < {min_points}")

    def resid(p: np.ndarray) -> np.ndarray:
        model = nss_loadings(t, p[4], p[5]) @ p[:4]
        return np.concatenate([(model - y) * 100.0, np.sqrt(NSS_RIDGE) * 1e2 * p[2:4]])

    def refine(x0: np.ndarray):
        x0 = np.clip(x0, NSS_LOWER + 1e-9, NSS_UPPER - 1e-9)
        return least_squares(resid, x0, bounds=(NSS_LOWER, NSS_UPPER), xtol=1e-10, ftol=1e-10)

    def rmse(sol) -> float:
        return float(np.sqrt(np.mean(((nss_loadings(t, *sol.x[4:]) @ sol.x[:4]) - y) ** 2)) * 100)

    best = None
    if prev is not None:  # warm start; the grid only runs when the warm fit isn't good
        best = refine(np.array([prev[k] for k in NSS_NAMES]))
    if best is None or rmse(best) >= WARM_OK_BP:
        for x0 in _grid_starts(t, y):
            sol = refine(x0)
            if best is None or sol.cost < best.cost:
                best = sol
    p = dict(zip(NSS_NAMES, (float(v) for v in best.x), strict=True))
    res = (nss_yield(t, p) - y) * 100.0
    return NssFit(
        p,
        float(np.sqrt(np.mean(res**2))),
        {float(k): float(v) for k, v in zip(t, res, strict=True)},
        n,
    )


def fit_history(
    panel: pd.DataFrame,
    curve: str,
    prev: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Daily fits over a panel (rows = obs_date). Only points observed that day are used.

    Returns rows for storage.curve_fits: [curve, obs_date, params_json, rmse, n_points, note].
    """
    rows = []
    for d, row in panel.iterrows():
        obs = row.dropna()
        fit = fit_nss(list(obs.index), list(obs.values), prev)
        if fit.ok:
            prev = fit.params
        rows.append(
            (
                curve,
                d.date(),
                json.dumps(fit.params) if fit.ok else None,
                fit.rmse_bp if fit.ok else None,
                fit.n_points,
                fit.note,
            )
        )
    return pd.DataFrame(
        rows, columns=["curve", "obs_date", "params_json", "rmse", "n_points", "note"]
    )


# ------------------------------------------------------------------ forwards

FORWARDS = {"1y1y": (1.0, 2.0), "2y1y": (2.0, 3.0), "2y3y": (2.0, 5.0), "5y5y": (5.0, 10.0)}


def forward_rate(y_a: float, a: float, y_b: float, b: float) -> float:
    """Annually compounded forward (%) between a and b years from spot rates (%)."""
    if b <= a:
        raise ValueError("b must be > a")
    g = (1 + y_b / 100) ** b / (1 + y_a / 100) ** a
    return (g ** (1 / (b - a)) - 1) * 100


def nss_forward(params: dict[str, float], a: float, b: float) -> float:
    ya, yb = nss_yield(np.array([a, b]), params)
    return forward_rate(float(ya), a, float(yb), b)


def observed_forward(curve_today: pd.DataFrame, a: float, b: float) -> float | None:
    """Forward from two observed points (bond curves: no fit). curve_today from curve_asof."""
    c = curve_today.set_index("tenor")["value"]
    if a not in c.index or b not in c.index:
        return None
    return forward_rate(float(c[a]), a, float(c[b]), b)


# ------------------------------------------------------------ slopes and flies

SLOPES = {"2s10s": (2.0, 10.0), "5s10s": (5.0, 10.0), "10s20s": (10.0, 20.0)}
FLIES = {"2s5s10s": (2.0, 5.0, 10.0), "5s10s20s": (5.0, 10.0, 20.0)}


def slope(panel: pd.DataFrame, a: float, b: float) -> pd.Series:
    """(y_b - y_a) in bp on dates where both tenors are observed; empty if a tenor is absent."""
    if a not in panel.columns or b not in panel.columns:
        return pd.Series(dtype=float)
    return ((panel[b] - panel[a]) * 100.0).dropna()


def fly(panel: pd.DataFrame, a: float, b: float, c: float) -> pd.Series:
    """(2 y_b - y_a - y_c) in bp where all three are observed."""
    if not {a, b, c} <= set(panel.columns):
        return pd.Series(dtype=float)
    return ((2 * panel[b] - panel[a] - panel[c]) * 100.0).dropna()


# ------------------------------------------------------------ rolling statistics


def window_start(end: date, window: str) -> date:
    """Start of a look-back window ending at `end`: 1W, 2W, 1M, 2M, 3M, 6M, 1Y, 2Y, 3Y."""
    n, unit = int(window[:-1]), window[-1]
    if unit == "W":
        return end - timedelta(weeks=n)
    if unit == "M":
        return end - relativedelta(months=n)
    if unit == "Y":
        return end - relativedelta(years=n)
    raise ValueError(f"bad window {window}")


def in_window(s: pd.Series, end: date, window: str) -> pd.Series:
    s = s.dropna()
    return s.loc[pd.Timestamp(window_start(end, window)) : pd.Timestamp(end)]


def percentile_of(values: pd.Series, x: float) -> float | None:
    """Percentile rank (0-100) of x within values (share of values <= x)."""
    v = values.dropna()
    if v.empty:
        return None
    return float((v <= x).mean() * 100.0)


def zscore_of(values: pd.Series, x: float) -> float | None:
    v = values.dropna()
    if len(v) < 2 or v.std() == 0:
        return None
    return float((x - v.mean()) / v.std())


def last_stats(s: pd.Series, window: str) -> dict[str, float | None]:
    """Current value, z-score and percentile of the last observation within a window."""
    s = s.dropna()
    if s.empty:
        return {"value": None, "z": None, "pct": None, "n": 0}
    w = in_window(s, s.index[-1].date(), window)
    x = float(s.iloc[-1])
    return {"value": x, "z": zscore_of(w, x), "pct": percentile_of(w, x), "n": len(w)}


def box_stats(panel: pd.DataFrame, end: date, window: str, today: pd.DataFrame) -> pd.DataFrame:
    """Per-tenor boxplot stats over a window (min/p25/median/p75/max, n) + today's percentile."""
    now = today.set_index("tenor")["value"] if not today.empty else pd.Series(dtype=float)
    rows = []
    for t in panel.columns:
        w = in_window(panel[t], end, window)
        if w.empty:
            continue
        x = float(now[t]) if t in now.index else None
        q = w.quantile([0.25, 0.5, 0.75])
        rows.append((float(t), len(w), w.min(), q[0.25], q[0.5], q[0.75], w.max(), x,
                     percentile_of(w, x) if x is not None else None))  # fmt: skip
    cols = ["tenor", "n", "min", "p25", "median", "p75", "max", "today", "pct"]
    return pd.DataFrame(rows, columns=cols)


# ----------------------------------------------------------------------- PCA


@dataclass(frozen=True)
class PcaResult:
    loadings: pd.DataFrame  # tenor x PC
    explained: pd.Series  # share of variance per PC
    scores: pd.DataFrame  # obs_date x PC, cumulative (bp)
    n_obs: int


PC_NAMES = ("Level", "Slope", "Curvature")


def pca_changes(panel: pd.DataFrame, tenors: Sequence[float], n: int = 3) -> PcaResult | None:
    """PCA on daily changes (bp) of dates where all `tenors` are observed.

    Signs: level loadings sum > 0; slope rises with tenor; curvature is positive in the belly.
    Changes span consecutive common dates (a gap makes one change cover several days).
    """
    tenors = [t for t in tenors if t in panel.columns]
    if len(tenors) < n:
        return None
    lv = panel[tenors].dropna()
    d = lv.diff().dropna() * 100.0
    if len(d) < 30:
        return None
    x = d - d.mean()
    _, s, vt = np.linalg.svd(x.values, full_matrices=False)
    load = vt[:n].T
    mid = len(tenors) // 2
    for k in range(n):
        col = load[:, k]
        flip = (
            (k == 0 and col.sum() < 0)
            or (k == 1 and col[-1] - col[0] < 0)
            or (k == 2 and col[mid] - (col[0] + col[-1]) / 2 < 0)
        )
        if flip:
            load[:, k] = -col
    names = list(PC_NAMES[:n]) + [f"PC{i + 1}" for i in range(3, n)]
    var = s**2 / np.sum(s**2)
    loadings = pd.DataFrame(load, index=pd.Index(tenors, name="tenor"), columns=names)
    scores = pd.DataFrame(x.values @ load, index=d.index, columns=names).cumsum()
    return PcaResult(loadings, pd.Series(var[:n], index=names), scores, len(d))


# --------------------------------------------------------------- carry & roll

HORIZON_YEARS = {"1M": 1 / 12, "3M": 0.25}
HORIZON_BD = {"1M": 21, "3M": 63}


def annuity_duration(y_pct: float, tenor: float) -> float:
    """Yield sensitivity (years) of a par annual-pay instrument; simple below 1y."""
    y = y_pct / 100
    if tenor <= 1:
        return tenor / (1 + y * tenor)
    if abs(y) < 1e-9:
        return tenor
    return (1 - (1 + y) ** -tenor) / y


def interp_linear(tenors: Sequence[float], values: Sequence[float], t: float) -> float | None:
    """Linear interpolation inside the observed range only (None outside: no extrapolation)."""
    tt = np.asarray(tenors, dtype=float)
    vv = np.asarray(values, dtype=float)
    order = np.argsort(tt)
    tt, vv = tt[order], vv[order]
    if len(tt) == 0 or t < tt[0] - 1e-12 or t > tt[-1] + 1e-12:
        return None
    return float(np.interp(t, tt, vv))


def avg_path_rate(
    tpm0: float, steps: Sequence[tuple[date, float]], start: date, end: date, spread_bp: float = 0.0
) -> float:
    """Average (calendar-day) overnight rate over [start, end) for a TPM step path + spread.

    steps: (effective_date, TPM from that date). Before the first step the TPM is tpm0.
    """
    if end <= start:
        raise ValueError("end must be after start")
    total, d, rate = 0.0, start, tpm0
    sched = sorted(steps)
    for eff, r in sched:
        if eff <= start:
            rate = r
    for eff, r in sched:
        if start < eff < end:
            total += rate * (eff - d).days
            d, rate = eff, r
    total += rate * (end - d).days
    return total / (end - start).days + spread_bp / 100.0


@dataclass(frozen=True)
class CarryRoll:
    tenor: float
    yield_pct: float
    carry_bp: float
    roll_bp: float | None
    uf_bp: float | None
    total_bp: float | None


def carry_roll(
    tenor: float,
    y_now: float,
    y_rolled: float | None,
    funding_pct: float,
    h_years: float,
    uf_accrual_pct: float | None = None,
) -> CarryRoll:
    """Carry and rolldown over a horizon, in bp of yield (running, not annualized).

    carry = (y - funding) * h / D, roll = y(T) - y(T - h), UF = accrual over h / D (real only),
    with D the par annuity duration. Dividing returns by D makes every column a yield-equivalent,
    comparable with yield volatility. Receiver / long-bond perspective, funded overnight.
    """
    dur = annuity_duration(y_now, tenor)
    carry = (y_now - funding_pct) * 100 * h_years / dur
    roll = None if y_rolled is None else (y_now - y_rolled) * 100
    uf = None if uf_accrual_pct is None else uf_accrual_pct * 100 / dur
    total = None if roll is None else carry + roll + (uf or 0.0)
    return CarryRoll(tenor, y_now, carry, roll, uf, total)


def realized_vol_bp(s: pd.Series, end: date, window: str = "3M") -> float | None:
    """Std of daily changes (bp) over the window (changes between consecutive observations)."""
    w = in_window(s, end, window)
    d = w.diff().dropna() * 100.0
    return float(d.std()) if len(d) >= 10 else None


def curve_deltas(panel: pd.DataFrame, ref: date, horizon: str, factor: float) -> pd.DataFrame:
    """Per tenor: today vs as-of the horizon, Δ = (now - then) x factor (100 for % -> bp).

    Only tenors observed in both curves (after hiding points older than MAX_AGE_BD).
    """
    now = curve_asof(panel, ref).set_index("tenor")
    then = curve_asof(panel, horizon_target(ref, horizon)).set_index("tenor")
    common = [t for t in now.index if t in then.index]
    out = pd.DataFrame(
        {
            "tenor": common,
            "now": [now.loc[t, "value"] for t in common],
            "now_date": [now.loc[t, "obs_date"] for t in common],
            "then": [then.loc[t, "value"] for t in common],
            "then_date": [then.loc[t, "obs_date"] for t in common],
        }
    )
    out["delta_bp"] = (out["now"] - out["then"]) * factor
    return out
