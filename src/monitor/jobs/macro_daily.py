"""Daily job for the Chile Macro tab: BDE macro series (incl. UF, breakevens, EEE/EOF) and the
INE release calendar. Writes to ingest_log.

Usage: uv run python -m monitor.jobs.macro_daily [--no-calendar]
"""

from __future__ import annotations

import argparse
import logging

from monitor import storage
from monitor.jobs import bcch_series
from monitor.sources.ine import IneSource

TABS = ["chile_macro", "curves_rates"]


def run(calendar: bool = True) -> dict[str, object]:
    conn = storage.connect()
    try:
        out: dict[str, object] = {"bde": bcch_series.run(tabs=TABS, conn=conn)}
        if calendar:
            try:
                with storage.ingest_run(conn, "macro_daily.ine_calendar", "ine") as r:
                    r.rows = storage.upsert_releases(conn, IneSource().fetch())
                out["ine_calendar"] = r.rows
            except Exception as e:  # noqa: BLE001
                out["ine_calendar"] = f"error: {e}"
    finally:
        conn.close()
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-calendar", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for part, res in run(not a.no_calendar).items():
        print(f"== {part}")
        if isinstance(res, dict):
            errs = {k: v for k, v in res.items() if isinstance(v, str)}
            print(f"  {len(res)} series, {len(errs)} errors {errs if errs else ''}")
        else:
            print(f"  {res}")


if __name__ == "__main__":
    main()
