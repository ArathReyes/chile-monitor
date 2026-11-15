"""Bond Monitor analytics (spec 04). Pure functions, no I/O.

Nominal only (user decision 2026-11-15): no DV01, duration or pricing. Sizes are converted to
CLP and USD; supply is aggregated in nominal terms.

Units
-----
* Letras and BTP: millions of CLP ("MM CLP"); BTU: thousands of UF ("M UF").
* CLP = MM CLP x 1e6, or M UF x 1e3 x UF(date); USD = CLP / dólar observado(date). For a date
  without its own UF / USD print (weekends, future auctions) the last value on or before it is
  used and reported (as-of rule, never interpolated).

Curve yield at a bond's maturity (concession proxy, event study)
----------------------------------------------------------------
y(T) = NSS swap fit(T) + (bond − swap) spread observed at the BDE benchmark tenors, linearly
interpolated in T and held flat outside the observed range. Nominal: SPC CLP fit + (BCP − SPC)
at 2/5/10Y; real: SPC UF fit + (BCU − SPC UF) at 2/5/10/20/30Y. This is a proxy built from
benchmark tenors, not a bond-level yield.

Concession (bp) = clearing rate − curve yield at the bond's maturity on T−1. Positive = the auction
cleared cheap to the curve. Labelled "vs fitted curve T−1": not a when-issued tail.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from monitor.models.curves import interp_linear, nss_yield

BUCKETS = ((0, 3, "0–3y"), (3, 7, "3–7y"), (7, 12, "7–12y"), (12, 20, "12–20y"), (20, 1e9, "20y+"))
BUCKET_ORDER = [b[2] for b in BUCKETS]


# ------------------------------------------------------------------ units


def asof_value(s: pd.Series, d: date) -> tuple[float | None, date | None]:
    """Last observation on or before d (and its date)."""
    s = s.dropna()
    s = s.loc[: pd.Timestamp(d)]
    if s.empty:
        return None, None
    return float(s.iloc[-1]), s.index[-1].date()


def to_clp(amount: float, unit: str, uf: float | None) -> float | None:
    """Native auction/outstanding units -> CLP."""
    if amount is None or pd.isna(amount):
        return None
    if unit == "MM CLP":
        return float(amount) * 1e6
    if unit == "M UF":
        return None if uf is None else float(amount) * 1e3 * uf
    raise ValueError(f"unknown unit {unit!r}")


def to_usd(clp: float | None, usdclp: float | None) -> float | None:
    if clp is None or usdclp is None or usdclp == 0:
        return None
    return clp / usdclp


def add_nominal_amounts(
    df: pd.DataFrame, amount_col: str, uf: pd.Series, usdclp: pd.Series, date_col: str
) -> pd.DataFrame:
    """Add <amount>_clp, <amount>_usd and the UF / USD dates used (as-of the row's date)."""
    out = df.copy()
    clp, usd, fx_dates = [], [], []
    for r in out.itertuples(index=False):
        d = pd.Timestamp(getattr(r, date_col)).date()
        u, _ = asof_value(uf, d)
        x, xd = asof_value(usdclp, d)
        c = to_clp(getattr(r, amount_col), r.unit, u)
        clp.append(c)
        usd.append(to_usd(c, x))
        fx_dates.append(xd)
    out[f"{amount_col}_clp"] = clp
    out[f"{amount_col}_usd"] = usd
    out["fx_date"] = fx_dates
    return out


# ------------------------------------------------------------------ buckets & supply


def years_between(d0: date, d1: date) -> float:
    return (pd.Timestamp(d1) - pd.Timestamp(d0)).days / 365.0


def tenor_bucket(years: float | None) -> str | None:
    if years is None or pd.isna(years):
        return None
    for lo, hi, name in BUCKETS:
        if lo <= years < hi:
            return name
    return None


def is_real(instrument_type: str | None) -> bool:
    return instrument_type == "BTU"


def week_start(d: date) -> date:
    d = pd.Timestamp(d).date()
    return d - timedelta(days=d.weekday())


def weekly_supply(cal: pd.DataFrame, value_col: str = "amount_usd") -> pd.DataFrame:
    """Sum of auction sizes by week (Monday), nominal/real and tenor bucket.

    cal needs: auction_date, maturity, instrument_type, and value_col.
    """
    if cal.empty:
        return pd.DataFrame(columns=["week", "segment", "bucket", value_col])
    d = cal.copy()
    d["week"] = [week_start(x) for x in d["auction_date"]]
    d["segment"] = ["Real (UF)" if is_real(t) else "Nominal (CLP)" for t in d["instrument_type"]]
    d["bucket"] = [
        tenor_bucket(years_between(a, m)) if pd.notna(m) else None
        for a, m in zip(d["auction_date"], d["maturity"], strict=True)
    ]
    g = d.groupby(["week", "segment", "bucket"], dropna=False)[value_col].sum(min_count=1)
    return g.reset_index()


# ------------------------------------------------------------------ results metrics


def allocation_shares(allocated: float | None, banks: float | None, others: float | None):
    """(% banks, % AFP/insurers/brokers). Hacienda writes '-' for zero allocation."""
    if allocated is None or pd.isna(allocated) or allocated == 0:
        return None, None
    b = 0.0 if banks is None or pd.isna(banks) else float(banks)
    o = 0.0 if others is None or pd.isna(others) else float(others)
    return 100 * b / allocated, 100 * o / allocated


def concession_bp(clearing_rate: float | None, curve_yield: float | None) -> float | None:
    """Clearing rate − curve yield at the bond's maturity (T−1), bp. Positive = cheap."""
    if clearing_rate is None or curve_yield is None:
        return None
    if pd.isna(clearing_rate) or pd.isna(curve_yield):
        return None
    return (float(clearing_rate) - float(curve_yield)) * 100.0


def btc_terciles(btc: pd.Series) -> pd.Series:
    """Low / Mid / High bid-to-cover terciles (NaN stays NaN)."""
    valid = btc.dropna()
    if len(valid) < 3:
        return pd.Series(index=btc.index, dtype=object)
    return pd.qcut(btc, 3, labels=["Low", "Mid", "High"]).astype(object)


# ------------------------------------------------------------------ curve yield proxy


@dataclass(frozen=True)
class CurveDay:
    """Inputs for one day: NSS swap params and observed bond−swap spreads (pp) by tenor."""

    params: Mapping[str, float] | None
    spreads: Mapping[float, float]


def curve_yield(t_years: float, day: CurveDay) -> float | None:
    """Swap NSS fit at t plus the interpolated (flat outside) bond−swap spread."""
    if day.params is None or t_years is None or t_years <= 0:
        return None
    base = float(nss_yield(t_years, dict(day.params))[0])
    if not day.spreads:
        return base
    ts = sorted(day.spreads)
    t = min(max(t_years, ts[0]), ts[-1])
    sp = interp_linear(ts, [day.spreads[k] for k in ts], t)
    return base + (sp or 0.0)


def event_path(
    yields_by_offset: Mapping[int, float | None], ref_offset: int = -1
) -> dict[int, float | None]:
    """Yield changes in bp relative to the reference offset (T−1)."""
    ref = yields_by_offset.get(ref_offset)
    if ref is None:
        return {k: None for k in yields_by_offset}
    return {k: None if v is None else (v - ref) * 100.0 for k, v in yields_by_offset.items()}


# ------------------------------------------------------------------ cash-flow calendar


def _on_day(year: int, month: int, day: int) -> date:
    last = (pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)).day
    return date(year, month, min(day, last))


def coupon_dates(
    maturity: date, months: Sequence[int], issue: date | None, start: date, end: date
) -> list[date]:
    """Coupon dates in (start, end], on the maturity's day of month in the listed months,
    after the issue date and up to maturity."""
    out = []
    for y in range(start.year, min(end.year, maturity.year) + 1):
        for m in months:
            d = _on_day(y, m, maturity.day)
            if start < d <= end and d <= maturity and (issue is None or d > issue):
                out.append(d)
    return sorted(out)


def cashflow_calendar(
    master: pd.DataFrame, outstanding: pd.DataFrame, start: date, end: date
) -> pd.DataFrame:
    """Coupons and principal due in (start, end] on today's outstanding (native units).

    Bullet bonds; semiannual coupon = outstanding x coupon% / 2. A bond's coupon months come
    from bond_master.coupon_months (flagged where Hacienda's listing looks inconsistent).
    """
    out = master.merge(outstanding[["nemo", "amount", "unit"]], on="nemo", how="inner")
    rows = []
    for r in out.itertuples(index=False):
        mat = pd.Timestamp(r.maturity_date).date() if pd.notna(r.maturity_date) else None
        if mat is None:
            continue
        issue = pd.Timestamp(r.issue_date).date() if pd.notna(r.issue_date) else None
        if r.coupon_freq and r.coupon_months:
            months = [int(m) for m in str(r.coupon_months).split(",")]
            for d in coupon_dates(mat, months, issue, start, end):
                rows.append((d, r.nemo, r.instrument_type, "coupon",
                             r.amount * r.coupon_pct / 100 / 2, r.unit))  # fmt: skip
        if start < mat <= end:
            rows.append((mat, r.nemo, r.instrument_type, "principal", float(r.amount), r.unit))
    cols = ["date", "nemo", "instrument_type", "kind", "amount", "unit"]
    return pd.DataFrame(rows, columns=cols).sort_values("date").reset_index(drop=True)


def straight_line_pace(year: int, authorized: float, on: date) -> float:
    """Authorized amount x share of the calendar year elapsed by `on`."""
    start, end = date(year, 1, 1), date(year, 12, 31)
    frac = (min(max(on, start), end) - start).days / (end - start).days
    return authorized * frac
