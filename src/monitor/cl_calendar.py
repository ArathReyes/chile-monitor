"""Chilean business-day calendar (public + bank holidays, via the `holidays` package)."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays


@lru_cache(maxsize=64)
def _holidays(year: int) -> holidays.HolidayBase:
    return holidays.country_holidays("CL", years=year, categories=("public", "bank"))


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _holidays(d.year)


def next_business_day(d: date) -> date:
    """First business day strictly after d."""
    d += timedelta(days=1)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def previous_business_day(d: date) -> date:
    """Last business day strictly before d."""
    d -= timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def following(d: date) -> date:
    """d itself if a business day, else the next one."""
    return d if is_business_day(d) else next_business_day(d)


def business_days(start: date, end: date) -> list[date]:
    """Business days in [start, end)."""
    out, d = [], following(start)
    while d < end:
        out.append(d)
        d = next_business_day(d)
    return out


def add_business_days(d: date, n: int) -> date:
    """d plus n Chilean business days (n >= 0); n = 0 returns following(d)."""
    out = following(d)
    for _ in range(n):
        out = next_business_day(out)
    return out


SPC_SPOT_LAG = 2  # Cámara swaps (SPC) settle T+2 (confirmed by the user, 2026-11-15)


def spc_start(trade_date: date) -> date:
    """Accrual start of an SPC traded/quoted on trade_date."""
    return add_business_days(trade_date, SPC_SPOT_LAG)
