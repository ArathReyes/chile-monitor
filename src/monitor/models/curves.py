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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from dateutil.relativedelta import relativedelta

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


def curve_panel(wide: pd.DataFrame, points: Sequence[CurvePoint]) -> pd.DataFrame:
    """obs_date x tenor_years panel of rates on the 365 basis; NaN where a tenor didn't trade."""
    cols = {}
    for p in sorted(points, key=lambda p: p.tenor):
        s = wide[p.series_id] if p.series_id in wide.columns else pd.Series(dtype=float)
        cols[p.tenor] = to_365(s.astype(float), p.basis)
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
        sa = to_365(wide[a].dropna(), bases.get(a, TARGET_BASIS))
        sb = to_365(wide[b].dropna(), bases.get(b, TARGET_BASIS))
        sp = spread_series(sa, sb)
        if sp.empty:
            continue
        frames.append(pd.DataFrame({"series_id": s.series_id, "obs_date": sp.index, "value": sp}))
    if not frames:
        return pd.DataFrame(columns=["series_id", "obs_date", "value"])
    return pd.concat(frames, ignore_index=True)
