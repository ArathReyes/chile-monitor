"""Daily job for the Chile Macro tab: BDE macro series (incl. UF, breakevens, EEE/EOF) and the
INE release calendar. Writes to ingest_log.

Usage: uv run python -m monitor.jobs.macro_daily [--no-calendar]
"""

from __future__ import annotations

import argparse
import logging

import pandas as pd

from monitor import storage
from monitor.jobs import bcch_series
from monitor.models.macro_surprises import build_surprises
from monitor.models.transforms import yoy
from monitor.sources.ine import IneSource

TABS = ["chile_macro", "curves_rates"]

# (indicator, actual series, transform, consensus series, survey->reference month offset)
SURPRISE_SPECS = [
    ("cpi_mm", "cl_cpi_mm_hist", None, "eee_cpi_mm_this_month", 0),
    ("imacec_yoy", "cl_imacec", "yoy", "eee_imacec_prev_month", -1),
    ("imacec_nm_yoy", "cl_imacec_nonmining", "yoy", "eee_imacec_nm_prev_month", -1),
]


def compute_surprises(conn) -> pd.DataFrame:
    """Surprises for every indicator with an EEE consensus; IMACEC uses revised data."""
    frames = []
    for ind, actual_id, transform, cons_id, offset in SURPRISE_SPECS:
        actual = storage.read_series(conn, actual_id).set_index("obs_date")["value"]
        if transform == "yoy":
            actual = yoy(actual).round(1)
        cons = storage.read_series(conn, cons_id).set_index("obs_date")["value"]
        df = build_surprises(ind, actual, cons, offset)
        df["consensus_source"] = "EEE"
        df["actual_is_first_print"] = ind == "cpi_mm"  # CPI essentially unrevised
        df["revised"] = None
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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
        try:
            with storage.ingest_run(conn, "macro_daily.surprises", "derived") as r:
                r.rows = storage.replace_release_surprises(conn, compute_surprises(conn))
            out["surprises"] = r.rows
        except Exception as e:  # noqa: BLE001
            out["surprises"] = f"error: {e}"
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
