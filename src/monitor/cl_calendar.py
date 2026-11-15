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
