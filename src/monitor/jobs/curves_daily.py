"""Daily job for the Curves & Rates tab: BDE curve series, then derived spreads (and NSS fits).

Usage: uv run python -m monitor.jobs.curves_daily [--full]
"""

from __future__ import annotations

import argparse
import logging

import duckdb

from monitor import storage
from monitor.config import load_series
from monitor.jobs import bcch_series
from monitor.models import curves

log = logging.getLogger(__name__)
TAB = "curves_rates"


def compute_derived(conn: duckdb.DuckDBPyConnection) -> int:
    """Recompute every derived spread from stored legs (full history; cheap) and upsert."""
    specs = [s for s in load_series().series if s.tab == TAB]
    derived = [s for s in specs if s.derived]
    storage.upsert_series_meta(conn, derived)
    legs = sorted({leg for s in derived for leg in curves.derived_legs(s.source_code)})
    wide = storage.read_wide(conn, legs)
    bases = {s.series_id: s.basis or curves.TARGET_BASIS for s in specs if not s.derived}
    df = curves.compute_derived(wide, derived, bases)
    return storage.upsert_observations(conn, df, "derived")


def run(full: bool = False) -> dict[str, object]:
    conn = storage.connect()
    try:
        out: dict[str, object] = {"bde": bcch_series.run(tabs=[TAB], full=full, conn=conn)}
        try:
            with storage.ingest_run(conn, "curves_daily.derived_spreads", "derived") as r:
                r.rows = compute_derived(conn)
            out["derived"] = r.rows
        except Exception as e:  # noqa: BLE001  (logged to ingest_log)
            out["derived"] = f"error: {e}"
    finally:
        conn.close()
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for part, res in run(a.full).items():
        print(f"== {part}")
        if isinstance(res, dict):
            errs = {k: v for k, v in res.items() if isinstance(v, str)}
            print(f"  {len(res)} series, {len(errs)} errors {errs if errs else ''}")
        else:
            print(f"  {res}")


if __name__ == "__main__":
    main()
