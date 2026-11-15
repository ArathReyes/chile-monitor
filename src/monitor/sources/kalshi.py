"""Kalshi Trade API v2, public market data (no key needed for reads).
Docs: https://docs.kalshi.com/getting_started/quick_start_market_data
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from monitor.http import HttpClient, HttpError
from monitor.sources.base import HealthResult, Source

BCCH_SERIES = "KXCBDCHILE"


class KalshiSource(Source):
    name = "kalshi"

    def fetch(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        raise NotImplementedError("kalshi ingestion comes with its spec")

    def health_check(self) -> HealthResult:
        try:
            with HttpClient() as client:
                resp = client.get(
                    f"{self.spec.base_url}/markets",
                    params={"series_ticker": BCCH_SERIES, "limit": "100"},
                )
            payload = resp.json()
        except (HttpError, ValueError) as e:
            return HealthResult(False, str(e), "fail")
        markets = payload.get("markets")
        if markets is None:
            return HealthResult(False, "unexpected response: no 'markets' key", "fail")
        open_n = sum(1 for m in markets if m.get("status") in ("open", "active"))
        return HealthResult(True, f"{BCCH_SERIES}: {len(markets)} markets ({open_n} open)", "ok")
