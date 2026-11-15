"""Treasury auctions, evening catch-up: latest result again, plus the current year's
consolidated file (fills auctions the live page no longer shows; live rows are never
overwritten).

Schedule: weekdays 18:00 New York. Usage: uv run python -m monitor.jobs.auctions_evening
"""

from __future__ import annotations

import logging

from monitor import storage
from monitor.sources import hacienda as h


def run() -> dict[str, object]:
    conn = storage.connect()
    out: dict[str, object] = {}
    try:
        try:
            out["results"] = h.run_results(conn)
        except Exception as e:  # noqa: BLE001
            out["results"] = f"error: {e}"
        try:
            out["consolidated"] = h.backfill(conn, [h.santiago_today().year])
        except Exception as e:  # noqa: BLE001
            out["consolidated"] = f"error: {e}"
        out["still_missing"] = h.missing_result_dates(conn)
    finally:
        conn.close()
    return out


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    for k, v in run().items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
