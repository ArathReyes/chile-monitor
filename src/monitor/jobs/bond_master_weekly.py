"""Bond master (Stock Total + Características Financieras) and outstanding amounts; also reloads
every consolidated results file so late corrections by Hacienda are picked up (logged as
revisions; live rows are never overwritten).

Schedule: Mondays 07:00 New York. Usage: uv run python -m monitor.jobs.bond_master_weekly
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
            out["consolidated"] = h.backfill(conn)
        except Exception as e:  # noqa: BLE001
            out["consolidated"] = f"error: {e}"
        try:
            out["master"] = h.run_master(conn)  # after the backfill: picks up every seen nemo
        except Exception as e:  # noqa: BLE001
            out["master"] = f"error: {e}"
    finally:
        conn.close()
    return out


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    for k, v in run().items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
