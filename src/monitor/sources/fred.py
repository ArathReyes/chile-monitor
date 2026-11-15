"""FRED API (St. Louis Fed). Docs: https://fred.stlouisfed.org/docs/api/fred/"""

from __future__ import annotations

from typing import Any

import pandas as pd

from monitor.http import HttpClient, HttpError
from monitor.sources.base import HealthResult, Source


class FredSource(Source):
    name = "fred"

    def fetch(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        raise NotImplementedError("fred ingestion comes with its spec")

    def health_check(self) -> HealthResult:
        if (missing := self.missing_key()) is not None:
            return missing
        params = {
            "series_id": "DGS10",
            "api_key": self.secret(),
            "file_type": "json",
            "sort_order": "desc",
            "limit": "1",
        }
        try:
            with HttpClient() as client:
                resp = client.get(f"{self.spec.base_url}/series/observations", params=params)
            obs = resp.json().get("observations", [])
        except (HttpError, ValueError) as e:
            return HealthResult(False, str(e), "fail")
        if not obs:
            return HealthResult(False, "key accepted but DGS10 returned no observations", "fail")
        return HealthResult(True, f"DGS10 {obs[0]['value']} on {obs[0]['date']}", "ok")
