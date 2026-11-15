"""Polymarket Gamma API (public, no key). BCCh decision markets.

Endpoints confirmed against live responses on 2026-11-14 (fixtures: tests/fixtures/polymarket_*):
  GET https://gamma-api.polymarket.com/public-search?q=...   -> {"events": [...]}
  GET https://gamma-api.polymarket.com/events?slug=...       -> [event with markets]
Each event is a negative-risk group of Yes/No markets; ``groupItemTitle`` names the outcome.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

import pandas as pd

from monitor.http import HttpClient, HttpError
from monitor.sources.base import HealthResult, Source
from monitor.sources.kalshi import OUTCOME_COLUMNS

BASE = "https://gamma-api.polymarket.com"
SEARCH_QUERY = "Chile interest rate"
SLUG_PREFIX = "central-bank-of-chile-decision-in-"


def outcome_bp(title: str) -> tuple[int, bool]:
    """Map an outcome title to (change_bp, is_open_bucket).

    '25 bps decrease' -> (-25, False); '50+ bps increase' -> (50, True); 'No Change' -> (0, False).
    """
    t = title.strip().lower()
    if t in ("no change", "no changes", "hold"):
        return 0, False
    m = re.fullmatch(r"(\d+)(\+?) ?bps? (increase|decrease|hike|cut)", t)
    if not m:
        raise ValueError(f"Unknown Polymarket outcome: {title!r}")
    sign = 1 if m[3] in ("increase", "hike") else -1
    return sign * int(m[1]), bool(m[2])


def event_meeting_date(event: dict[str, Any]) -> date:
    m = re.search(r"scheduled for ([A-Z][a-z]+ \d{1,2}, \d{4})", event.get("description") or "")
    if m:
        return datetime.strptime(m[1], "%B %d, %Y").date()
    return datetime.fromisoformat(event["endDate"].replace("Z", "+00:00")).date()


def parse_event(event: dict[str, Any]) -> pd.DataFrame:
    meeting = event_meeting_date(event)
    rows = []
    for m in event.get("markets", []):
        bp, bucket = outcome_bp(m["groupItemTitle"])
        rows.append(
            {
                "venue": "polymarket",
                "event": event["slug"],
                "meeting_date": meeting,
                "market": m.get("slug") or m.get("question"),
                "outcome": m["groupItemTitle"],
                "change_bp": bp,
                "bucket": bucket,
                "yes_bid": m.get("bestBid"),
                "yes_ask": m.get("bestAsk"),
                "last": m.get("lastTradePrice"),
                "volume": float(m["volume"]) if m.get("volume") else None,
                "open_interest": None,
                "status": "closed" if m.get("closed") else "active",
            }
        )
    return pd.DataFrame(rows, columns=OUTCOME_COLUMNS)


def outcome_yes_price(market: dict[str, Any]) -> float | None:
    prices = market.get("outcomePrices")
    if not prices:
        return None
    return float(json.loads(prices)[0])


class PolymarketSource(Source):
    name = "polymarket"

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        with HttpClient() as client:
            return client.get(f"{BASE}{path}", params=params).json()

    def find_bcch_slugs(self, include_closed: bool = False) -> list[str]:
        events = self._get("/public-search", {"q": SEARCH_QUERY}).get("events", [])
        return [
            e["slug"]
            for e in events
            if e.get("slug", "").startswith(SLUG_PREFIX) and (include_closed or not e.get("closed"))
        ]

    def events(self, include_closed: bool = False) -> list[dict[str, Any]]:
        out = []
        for slug in self.find_bcch_slugs(include_closed):
            out.extend(self._get("/events", {"slug": slug}))
        return out

    def fetch(self, include_closed: bool = False) -> pd.DataFrame:
        frames = [parse_event(e) for e in self.events(include_closed)]
        return (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=OUTCOME_COLUMNS)
        )

    def health_check(self) -> HealthResult:
        try:
            slugs = self.find_bcch_slugs()
        except (HttpError, ValueError) as e:
            return HealthResult(False, str(e), "fail")
        if not slugs:
            return HealthResult(True, "reachable; no open BCCh markets found", "ok")
        return HealthResult(True, f"{len(slugs)} open BCCh decision events", "ok")
