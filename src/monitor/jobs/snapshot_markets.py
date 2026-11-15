"""Snapshot BCCh prediction markets (Kalshi + Polymarket) into market_snapshots.

Meant to run hourly, and on Refresh. Kalshi daily candlesticks are backfilled on each run
(source "kalshi_candles", one row per market per day, de-duplicated).

Usage: uv run python -m monitor.jobs.snapshot_markets [--no-candles]
"""

from __future__ import annotations

import argparse
import json
import logging
import time

import duckdb
import pandas as pd

from monitor import storage
from monitor.sources.kalshi import KalshiSource, parse_candlesticks
from monitor.sources.polymarket import PolymarketSource

log = logging.getLogger(__name__)
CANDLE_LOOKBACK_DAYS = 120


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso"))


def snapshot_venue(conn: duckdb.DuckDBPyConnection, venue: str, df: pd.DataFrame) -> int:
    ts = storage.utcnow()
    for event, g in df.groupby("event"):
        storage.insert_market_snapshot(conn, venue, str(event), {"outcomes": _records(g)}, ts)
    return df["event"].nunique()


def backfill_kalshi_candles(conn: duckdb.DuckDBPyConnection, src: KalshiSource) -> int:
    df = src.fetch(status=None)
    now = int(time.time())
    start = now - CANDLE_LOOKBACK_DAYS * 86400
    have = set(
        conn.execute(
            "SELECT ticker || '|' || CAST(snapshot_ts AS VARCHAR) FROM market_snapshots "
            "WHERE source = 'kalshi_candles'"
        ).fetchall()
    )
    have = {h[0] for h in have}
    n = 0
    for market in df["market"]:
        candles = parse_candlesticks(src.candlesticks(market, start, now))
        for row in candles.itertuples(index=False):
            key = f"{market}|{row.ts}"
            if key in have:
                continue
            payload = {
                "yes_bid": row.yes_bid,
                "yes_ask": row.yes_ask,
                "open_interest": row.open_interest,
                "volume": row.volume,
            }
            storage.insert_market_snapshot(conn, "kalshi_candles", market, payload, row.ts)
            n += 1
    return n


def run(
    candles: bool = True, conn: duckdb.DuckDBPyConnection | None = None
) -> dict[str, int | str]:
    own = conn is None
    conn = conn or storage.connect()
    out: dict[str, int | str] = {}
    try:
        for venue, fetch in (
            ("kalshi", lambda: KalshiSource().fetch(status="open")),
            ("polymarket", lambda: PolymarketSource().fetch()),
        ):
            try:
                with storage.ingest_run(conn, f"snapshot_markets.{venue}", venue) as r:
                    r.rows = snapshot_venue(conn, venue, fetch())
                out[venue] = r.rows
            except Exception as e:
                out[venue] = f"error: {e}"
        if candles:
            try:
                with storage.ingest_run(conn, "snapshot_markets.kalshi_candles", "kalshi") as r:
                    r.rows = backfill_kalshi_candles(conn, KalshiSource())
                out["kalshi_candles"] = r.rows
            except Exception as e:
                out["kalshi_candles"] = f"error: {e}"
    finally:
        if own:
            conn.close()
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-candles", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    print(run(candles=not a.no_candles))


if __name__ == "__main__":
    main()
