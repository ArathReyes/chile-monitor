"""Treasury auctions, morning run: calendar (with change log), latest result, SOMA status.

Schedule: weekdays 07:30 New York. Usage: uv run python -m monitor.jobs.auctions_daily
"""

from __future__ import annotations

import logging

from monitor import storage
from monitor.sources import hacienda as h


def run() -> dict[str, object]:
    conn = storage.connect()
    out: dict[str, object] = {}
    try:
        for name, fn in (
            ("calendar", h.run_calendar),
            ("results", h.run_results),
            ("soma", h.run_soma),
        ):
            try:
                out[name] = fn(conn)
            except Exception as e:  # noqa: BLE001  (already in ingest_log)
                out[name] = f"error: {e}"
    finally:
        conn.close()
    return out


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    for k, v in run().items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
