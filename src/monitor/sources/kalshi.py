"""Kalshi Trade API v2, public market data (no key needed for reads).

Docs: https://docs.kalshi.com/getting_started/quick_start_market_data
Prices are dollar strings ("0.5800" = 58c = 58%). Field names confirmed against live responses
(fixtures: tests/fixtures/kalshi_*.json).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from monitor.http import HttpClient, HttpError
from monitor.sources.base import HealthResult, Source

BCCH_SERIES = "KXCBDCHILE"
BASES = (
    "https://external-api.kalshi.com/trade-api/v2",
    "https://api.elections.kalshi.com/trade-api/v2",
)
# Market ticker suffix -> policy-rate change in bp. "P" buckets ("more than 25bps") are
# represented as 50bp, the nearest 25bp outcome inside the bucket.
OUTCOME_BP = {"C25P": -50, "C25": -25, "HOLD": 0, "H25": 25, "H25P": 50}
OUTCOME_COLUMNS = [
    "venue", "event", "meeting_date", "market", "outcome", "change_bp", "bucket",
    "yes_bid", "yes_ask", "last", "volume", "open_interest", "status",
]  # fmt: skip


def _f(x: Any) -> float | None:
    if x in (None, ""):
        return None
    return float(x)


def event_meeting_date(event_ticker: str) -> date:
    """KXCBDCHILE-26DEC15 -> 2026-12-15."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker)
    if not m:
        raise ValueError(f"Unexpected Kalshi event ticker: {event_ticker}")
    return datetime.strptime(f"{m[1]}{m[2].title()}{m[3]}", "%y%b%d").date()


def parse_events(events: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten events-with-nested-markets into one row per outcome market."""
    rows = []
    for ev in events:
        meeting = event_meeting_date(ev["event_ticker"])
        for m in ev.get("markets", []):
            suffix = m["ticker"].rsplit("-", 1)[-1]
            if suffix not in OUTCOME_BP:
                raise ValueError(f"Unknown Kalshi outcome market {m['ticker']}")
            rows.append(
                {
                    "venue": "kalshi",
                    "event": ev["event_ticker"],
                    "meeting_date": meeting,
                    "market": m["ticker"],
                    "outcome": m.get("yes_sub_title") or suffix,
                    "change_bp": OUTCOME_BP[suffix],
                    "bucket": suffix.endswith("P"),
                    "yes_bid": _f(m.get("yes_bid_dollars")),
                    "yes_ask": _f(m.get("yes_ask_dollars")),
                    "last": _f(m.get("last_price_dollars")),
                    "volume": _f(m.get("volume_fp")),
                    "open_interest": _f(m.get("open_interest_fp")),
                    "status": m.get("status"),
                }
            )
    return pd.DataFrame(rows, columns=OUTCOME_COLUMNS)


def parse_candlesticks(payload: dict[str, Any]) -> pd.DataFrame:
    """Candles -> [ts, yes_bid, yes_ask, open_interest, volume] (close values, UTC-naive ts)."""
    rows = []
    for c in payload.get("candlesticks", []):
        rows.append(
            {
                "ts": datetime.fromtimestamp(c["end_period_ts"], UTC).replace(tzinfo=None),
                "yes_bid": _f(c.get("yes_bid", {}).get("close_dollars")),
                "yes_ask": _f(c.get("yes_ask", {}).get("close_dollars")),
                "open_interest": _f(c.get("open_interest_fp")),
                "volume": _f(c.get("volume_fp")),
            }
        )
    return pd.DataFrame(rows, columns=["ts", "yes_bid", "yes_ask", "open_interest", "volume"])


class KalshiSource(Source):
    name = "kalshi"

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_err: Exception | None = None
        with HttpClient() as client:
            for base in BASES:
                try:
                    return client.get(f"{base}{path}", params=params).json()
                except HttpError as e:  # try the fallback host
                    last_err = e
        raise HttpError(f"Kalshi unreachable on all hosts: {last_err}")

    def events(self, status: str | None = None, series: str = BCCH_SERIES) -> list[dict]:
        params: dict[str, Any] = {
            "series_ticker": series,
            "with_nested_markets": "true",
            "limit": 200,
        }
        if status:
            params["status"] = status
        return self._get("/events", params).get("events", [])

    def candlesticks(
        self, market: str, start_ts: int, end_ts: int, period_minutes: int = 1440,
        series: str = BCCH_SERIES,
    ) -> dict[str, Any]:  # fmt: skip
        return self._get(
            f"/series/{series}/markets/{market}/candlesticks",
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_minutes},
        )

    def fetch(self, status: str | None = "open") -> pd.DataFrame:
        return parse_events(self.events(status))

    def health_check(self) -> HealthResult:
        try:
            df = self.fetch(status=None)
        except (HttpError, ValueError, KeyError) as e:
            return HealthResult(False, str(e), "fail")
        n_open = df.loc[df.status == "active", "event"].nunique()
        return HealthResult(
            True, f"{BCCH_SERIES}: {df.event.nunique()} events ({n_open} open)", "ok"
        )
