"""Daily job for the Monetary Policy tab.

Runs: BDE series for the tab (TPM, SPC, TIB, EOF, EEE, CPI, IMACEC), manual BCCh documents
(saved pages/PDFs in data/manual/bcch_web/), and a prediction-market snapshot.
Each part writes to ingest_log (Data Health tab).

Usage: uv run python -m monitor.jobs.mp_daily [--no-download] [--no-markets]
"""

from __future__ import annotations

import argparse
import logging

from monitor import storage
from monitor.jobs import bcch_series, snapshot_markets
from monitor.sources import bcch_web

MP_TABS = ["monetary_policy", "chile_macro"]


def run(download: bool = True, markets: bool = True) -> dict[str, object]:
    conn = storage.connect()
    try:
        out: dict[str, object] = {
            "bde": bcch_series.run(tabs=MP_TABS, conn=conn),
            "documents": bcch_web.ingest_all(conn, download=download),
        }
        if markets:
            out["markets"] = snapshot_markets.run(candles=True, conn=conn)
    finally:
        conn.close()
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-download", action="store_true", help="parse saved files only")
    p.add_argument("--no-markets", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for part, res in run(not a.no_download, not a.no_markets).items():
        print(f"== {part}")
        if isinstance(res, dict):
            for k, v in res.items():
                print(f"  {k:26s} {v}")


if __name__ == "__main__":
    main()
