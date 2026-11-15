"""Pure helpers for data-staleness checks (used by the Data Health tab)."""

from __future__ import annotations

from datetime import date, timedelta

# Max calendar-day age of the latest observation before a series counts as stale.
# Generous on purpose: BDE daily rates are T-1 and transaction-based with gaps; monthly
# data is published weeks after the reference month.
MAX_AGE_DAYS: dict[str, int] = {"D": 5, "W": 12, "M": 70, "Q": 150, "A": 500}


def is_stale(last_obs: date | None, freq: str, today: date) -> bool:
    if last_obs is None:
        return True
    return today - last_obs > timedelta(days=MAX_AGE_DAYS.get(freq, 5))
