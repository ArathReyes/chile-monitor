"""Daily job for the Curves & Rates tab: BDE curve series, then derived spreads (and NSS fits).

Usage: uv run python -m monitor.jobs.curves_daily [--full]
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import timedelta

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


REFIT_DAYS = 10  # re-fit this many days before the last stored fit (late BDE revisions)


def compute_fits(conn: duckdb.DuckDBPyConnection, full: bool = False) -> dict[str, int]:
    """NSS fits for the swap curves, incremental and warm-started from the last stored fit."""
    specs = [s for s in load_series().series if s.tab == TAB]
    out = {}
    for key, cdef in curves.CURVES.items():
        if not cdef.fit:
            continue
        pts = curves.points_from_specs(specs, key)
        last = None if full else storage.last_curve_fit_date(conn, key)
        start = last - timedelta(days=REFIT_DAYS) if last else None
        prev = None
        if start is not None:
            hist = storage.read_curve_fits(conn, key)
            hist = hist[(hist["obs_date"] < str(start)) & hist["params_json"].notna()]
            prev = json.loads(hist["params_json"].iloc[-1]) if len(hist) else None
        wide = storage.read_wide(conn, [p.series_id for p in pts], start)
        panel = curves.curve_panel(wide, pts)
        out[key] = storage.upsert_curve_fits(conn, curves.fit_history(panel, key, prev=prev))
    return out


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
        try:
            with storage.ingest_run(conn, "curves_daily.nss_fits", "derived") as r:
                fits = compute_fits(conn, full)
                r.rows = sum(fits.values())
            out["nss_fits"] = fits
        except Exception as e:  # noqa: BLE001
            out["nss_fits"] = f"error: {e}"
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
