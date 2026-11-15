"""Auction-day watcher: on a day with an auction in the stored calendar, poll Hacienda's results
page every 15 minutes from 11:05 to 18:00 Santiago until today's result is stored. Exits at
once on other days.

Schedule: weekdays 08:00 New York. Usage: uv run python -m monitor.jobs.auctions_watch
"""

from __future__ import annotations

import logging

from monitor import storage
from monitor.sources import hacienda as h


def run() -> str:
    conn = storage.connect()
    try:  # refresh the calendar first so a same-day schedule change is honoured
        h.run_calendar(conn)
    except Exception as e:  # noqa: BLE001
        logging.warning("calendar refresh failed, using stored calendar: %s", e)
    finally:
        conn.close()
    return h.watch()  # opens the database per poll


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(run())


if __name__ == "__main__":
    main()
