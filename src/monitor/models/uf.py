"""Unidad de Fomento (UF): daily path, projection under CPI scenarios, real-vs-nominal carry.

UF rule (verified, not assumed)
-------------------------------
From the 10th of month t to the 9th of month t+1 the UF compounds daily and geometrically so
that its total change equals the CPI change of month t-1 (INE published m/m, rounded to 0.1pp):

    UF(9th of t + k days) = round( UF(9th of t) * (1 + pi_{t-1}) ** (k / N), 2 ),   k = 1..N

where N = number of days from the 9th of month t to the 9th of month t+1 (days in month t).
Source: reproduced exactly (max error 0.00 CLP) on every daily BDE UF value (F073.UFF.PRE.Z.D)
for all 177 monthly periods Jan 2012 – Nov 2026 using the INE official m/m CPI
(F074.IPC.VAR.Z.Z.C.M); see tests/test_uf.py. No official methodology document was consulted
(www.bcentral.cl blocks automated access); the empirical check is the citation.

Carry conventions (accrual carry only: no roll-down, no price change)
--------------------------------------------------------------------
* Real leg (BCU/BTU or Cámara swap UF): CLP return = (1 + r_real * tau) * UF_end / UF_start - 1.
* Nominal leg (BCP/BTP or Cámara swap CLP): CLP return = r_nom * tau.
* tau = days / basis (365 for bond yields, 360 for Cámara swaps).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from dateutil.relativedelta import relativedelta
from scipy.optimize import brentq


def period_anchor(d: date) -> date:
    """The 9th that starts the UF period containing d (periods run 10th..9th)."""
    nine = date(d.year, d.month, 9)
    return nine if d > nine else nine - relativedelta(months=1)


def cpi_month_for_period(anchor_9th: date) -> date:
    """Reference CPI month (1st of month) that drives the period starting on anchor_9th."""
    return (anchor_9th.replace(day=1)) - relativedelta(months=1)


def known_uf_path(uf_on_9th: float, anchor_9th: date, cpi_mm_pct: float) -> pd.Series:
    """Daily UF from the 10th after anchor_9th to the next 9th, fixed by one CPI print."""
    end = anchor_9th + relativedelta(months=1)
    n = (end - anchor_9th).days
    days = [anchor_9th + timedelta(days=k) for k in range(1, n + 1)]
    g = 1 + cpi_mm_pct / 100
    vals = [round(uf_on_9th * g ** (k / n), 2) for k in range(1, n + 1)]
    return pd.Series(vals, index=pd.to_datetime(days), name="uf")


def projected_uf_path(
    last_known_9th: date, uf_on_last_9th: float, cpi_path: dict[date, float], months: int
) -> pd.Series:
    """Extend the UF beyond the known window, `months` periods ahead.

    cpi_path: {reference CPI month (1st): m/m %}. Each period after last_known_9th uses the CPI
    of the month before the period's start; raises if a needed month is missing (no filling).
    """
    out, anchor, uf9 = [], last_known_9th, uf_on_last_9th
    for _ in range(months):
        ref = cpi_month_for_period(anchor)
        if ref not in cpi_path:
            raise ValueError(f"CPI scenario missing reference month {ref:%Y-%m}")
        seg = known_uf_path(uf9, anchor, cpi_path[ref])
        out.append(seg)
        anchor = anchor + relativedelta(months=1)
        uf9 = float(seg.iloc[-1])
    return pd.concat(out) if out else pd.Series(dtype=float, name="uf")


def uf_accrual_pct(uf_path: pd.Series, start: date, end: date) -> float:
    s = uf_path.copy()
    s.index = pd.to_datetime(s.index)
    a, b = s.get(pd.Timestamp(start)), s.get(pd.Timestamp(end))
    if a is None or b is None:
        raise ValueError(f"UF path doesn't cover {start} and {end}")
    return (float(b) / float(a) - 1) * 100


@dataclass(frozen=True)
class CarryResult:
    days: int
    uf_accrual_pct: float
    real_leg_pct: float
    nominal_leg_pct: float
    diff_bp: float  # real minus nominal, over the period
    diff_bp_annualized: float
    diff_clp: float


def uf_carry(
    uf_path: pd.Series,
    start: date,
    end: date,
    real_yield_pct: float,
    nominal_yield_pct: float,
    notional_clp: float = 1_000_000_000.0,
    basis: int = 365,
) -> CarryResult:
    """Accrual carry of a real (UF) position vs a nominal one over [start, end]."""
    days = (end - start).days
    if days <= 0:
        raise ValueError("end must be after start")
    tau = days / basis
    acc = uf_accrual_pct(uf_path, start, end)
    real = ((1 + real_yield_pct / 100 * tau) * (1 + acc / 100) - 1) * 100
    nominal = nominal_yield_pct * tau
    diff_bp = (real - nominal) * 100
    return CarryResult(
        days,
        acc,
        real,
        nominal,
        diff_bp,
        diff_bp * 365 / days,
        notional_clp * (real - nominal) / 100,
    )


def breakeven_monthly_cpi(
    known_path: pd.Series,
    last_known_9th: date,
    start: date,
    end: date,
    real_yield_pct: float,
    nominal_yield_pct: float,
    basis: int = 365,
) -> float:
    """Constant monthly CPI (%, for all unknown months) that makes real and nominal carry equal.

    If the holding period ends inside the known window, the UF is already fixed and there's no
    CPI to solve for: raises ValueError.
    """
    if pd.Timestamp(end) <= known_path.index.max():
        raise ValueError("holding period is inside the known UF window; nothing to solve")
    months = _periods_needed(last_known_9th, end)
    uf9 = float(known_path.loc[pd.Timestamp(last_known_9th)])

    def diff(m: float) -> float:
        cpi = {
            cpi_month_for_period(last_known_9th + relativedelta(months=i)): m for i in range(months)
        }
        path = pd.concat([known_path, projected_uf_path(last_known_9th, uf9, cpi, months)])
        return uf_carry(path, start, end, real_yield_pct, nominal_yield_pct, basis=basis).diff_bp

    return brentq(diff, -5.0, 5.0, xtol=1e-8)


def _periods_needed(last_known_9th: date, end: date) -> int:
    n, anchor = 0, last_known_9th
    while anchor < end:
        anchor = anchor + relativedelta(months=1)
        n += 1
    return n


def path_with_scenario(
    known_path: pd.Series, last_known_9th: date, cpi_path: dict[date, float], months: int
) -> pd.Series:
    """Known UF path followed by `months` projected periods under cpi_path."""
    uf9 = float(known_path.loc[pd.Timestamp(last_known_9th)])
    proj = projected_uf_path(last_known_9th, uf9, cpi_path, months)
    return pd.concat([known_path, proj])
