"""Ingest BCCh BDE series from the registry into DuckDB (with vintages).

Usage:
    uv run python -m monitor.jobs.bcch_series                # all bcch series, incremental
    uv run python -m monitor.jobs.bcch_series --full         # full history
    uv run python -m monitor.jobs.bcch_series --tab monetary_policy
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import timedelta

import duckdb

from monitor import storage
from monitor.config import SeriesSpec, load_series
from monitor.sources.bcch import BcchSource

log = logging.getLogger(__name__)

# Re-fetch this many days before the last stored observation to catch revisions.
REVISION_WINDOW = {"D": 30, "W": 60, "M": 400, "Q": 800, "A": 1500}


def eligible(specs: list[SeriesSpec]) -> list[SeriesSpec]:
    return [s for s in specs if s.source == "bcch" and s.verified and not s.is_placeholder]


def ingest_one(
    conn: duckdb.DuckDBPyConnection, src: BcchSource, spec: SeriesSpec, full: bool = False
) -> int:
    last = None if full else storage.last_obs_date(conn, spec.series_id)
    first = last - timedelta(days=REVISION_WINDOW.get(spec.freq, 30)) if last else None
    with storage.ingest_run(conn, f"bcch_series.{spec.series_id}", "bcch") as run:
        # no lastdate: some series are published ahead (e.g. the UF up to the 9th of next month)
        payload = src.request_series(spec.source_code, first, None)
        storage.save_raw(
            "bcch", f"{spec.series_id}.json", json.dumps(payload, ensure_ascii=False).encode()
        )
        df = src.parse(payload)
        df.insert(0, "series_id", spec.series_id)
        run.rows = storage.upsert_observations(conn, df, "bcch")
    return run.rows


def run(
    tabs: list[str] | None = None,
    series_ids: list[str] | None = None,
    full: bool = False,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, int | str]:
    """Ingest; returns {series_id: changed_rows or 'error: ...'}. Never raises per series."""
    specs = eligible(load_series().series)
    if tabs:
        specs = [s for s in specs if s.tab in tabs]
    if series_ids:
        specs = [s for s in specs if s.series_id in series_ids]
    own = conn is None
    conn = conn or storage.connect()
    src = BcchSource()
    results: dict[str, int | str] = {}
    try:
        storage.upsert_series_meta(conn, specs)
        for spec in specs:
            try:
                results[spec.series_id] = ingest_one(conn, src, spec, full)
            except Exception as e:  # logged to ingest_log by ingest_run
                results[spec.series_id] = f"error: {e}"
                log.warning("bcch %s failed: %s", spec.series_id, e)
    finally:
        if own:
            conn.close()
    return results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true")
    p.add_argument("--tab", action="append")
    p.add_argument("--series", action="append")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for sid, res in run(a.tab, a.series, a.full).items():
        print(f"{sid:28s} {res}")


if __name__ == "__main__":
    main()
