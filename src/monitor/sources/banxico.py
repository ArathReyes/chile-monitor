"""Banxico SIE REST API. Auth: ``Bmx-Token`` header (BANXICO_TOKEN)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from monitor.http import HttpClient, HttpError
from monitor.sources.base import HealthResult, Source

HEALTH_SERIES = "SF43718"  # USD/MXN FIX


class BanxicoSource(Source):
    name = "banxico"

    def fetch(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        raise NotImplementedError("banxico ingestion comes with its spec")

    def health_check(self) -> HealthResult:
        if (missing := self.missing_key()) is not None:
            return missing
        url = f"{self.spec.base_url}/series/{HEALTH_SERIES}/datos/oportuno"
        try:
            with HttpClient() as client:
                resp = client.get(url, headers={"Bmx-Token": self.secret()})
            series = resp.json()["bmx"]["series"][0]
            obs = series["datos"][-1]
        except (HttpError, ValueError, KeyError, IndexError) as e:
            return HealthResult(False, f"{type(e).__name__}: {e}", "fail")
        return HealthResult(True, f"FIX {obs['dato']} on {obs['fecha']}", "ok")
