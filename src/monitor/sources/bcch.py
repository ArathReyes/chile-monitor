"""BCCh Base de Datos Estadísticos (BDE) REST API.

Auth: ``token`` query parameter (BCCH_API_TOKEN). Request dates are YYYY-MM-DD; response
``Obs[].indexDateString`` is DD-MM-YYYY and ``value`` is a dot-decimal string ("NaN" = missing).
Docs: https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/documentacion.html
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any, Literal

import pandas as pd

from monitor.http import HttpClient, HttpError
from monitor.parsing import parse_bde_date, parse_bde_value
from monitor.sources.base import HealthResult, Source

Frequency = Literal["DAILY", "MONTHLY", "QUARTERLY", "ANNUAL"]
HEALTH_SERIES = "F022.TPM.TIN.D001.NO.Z.D"  # TPM; code from the official docs example
MIN_OBS_DATE = date(
    1900, 1, 1
)  # BDE placeholder date is 31-12-1899 (value 0); real history starts 1928+

log = logging.getLogger(__name__)


class BcchApiError(RuntimeError):
    pass


def _check(payload: dict[str, Any]) -> None:
    code = payload.get("Codigo")
    if code not in (0, None):
        raise BcchApiError(f"BDE error {code}: {payload.get('Descripcion', '')}")


def parse_get_series(payload: dict[str, Any]) -> pd.DataFrame:
    """Parse a GetSeries response into ``[obs_date, value]``, dropping missing values."""
    _check(payload)
    series = payload.get("Series")
    if not isinstance(series, dict) or "Obs" not in series:
        raise BcchApiError("Unexpected GetSeries payload: no Series.Obs")
    rows = []
    for obs in series["Obs"] or []:
        value = parse_bde_value(obs.get("value"))
        if value is None:
            continue
        d = parse_bde_date(obs["indexDateString"])
        if d < MIN_OBS_DATE:
            # BDE placeholder rows (e.g. 31-12-1899, value 0): drop, but keep the series.
            log.warning(
                "BDE %s: dropped observation dated %s (before %s)",
                series.get("seriesId", "?"),
                d,
                MIN_OBS_DATE,
            )
            continue
        rows.append((d, value))
    return pd.DataFrame(rows, columns=["obs_date", "value"])


def parse_search_series(payload: dict[str, Any]) -> pd.DataFrame:
    _check(payload)
    infos = payload.get("SeriesInfos")
    if not isinstance(infos, list):
        raise BcchApiError("Unexpected SearchSeries payload: no SeriesInfos list")
    return pd.DataFrame(infos)


class BcchSource(Source):
    name = "bcch"

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        token = self.secret()
        if not token:
            raise BcchApiError("BCCH_API_TOKEN not set in .env")
        with HttpClient() as client:
            resp = client.get(self.spec.base_url, params={"token": token, **params})
        # BDE declares charset=ISO-8859-1; resp.json() would assume UTF-8.
        return json.loads(resp.text)

    def request_series(
        self, code: str, firstdate: date | None = None, lastdate: date | None = None
    ) -> dict[str, Any]:
        """Raw GetSeries payload (for saving under data/raw before parsing)."""
        params = {"function": "GetSeries", "timeseries": code}
        if firstdate:
            params["firstdate"] = firstdate.isoformat()
        if lastdate:
            params["lastdate"] = lastdate.isoformat()
        return self._request(params)

    @staticmethod
    def parse(payload: dict[str, Any]) -> pd.DataFrame:
        return parse_get_series(payload)

    def get_series(
        self, code: str, firstdate: date | None = None, lastdate: date | None = None
    ) -> pd.DataFrame:
        return parse_get_series(self.request_series(code, firstdate, lastdate))

    def search_series(self, frequency: Frequency, query: str | None = None) -> pd.DataFrame:
        """List BDE series for a frequency, optionally filtered by a case-insensitive substring
        of the Spanish/English title or the series id. Use this to verify codes."""
        df = parse_search_series(
            self._request({"function": "SearchSeries", "frequency": frequency})
        )
        if query and not df.empty:
            cols = [c for c in ("seriesId", "spanishTitle", "englishTitle") if c in df.columns]
            text = df[cols].astype(str).agg(" ".join, axis=1)
            df = df[text.str.contains(query, case=False, regex=False)]
        return df.reset_index(drop=True)

    def fetch(
        self, code: str, firstdate: date | None = None, lastdate: date | None = None
    ) -> pd.DataFrame:
        """Tidy ``[obs_date, value]`` for one BDE code (see monitor.jobs.bcch_series)."""
        return self.get_series(code, firstdate, lastdate)

    def health_check(self) -> HealthResult:
        if (missing := self.missing_key()) is not None:
            return missing
        today = date.today()
        try:
            df = self.get_series(HEALTH_SERIES, today - timedelta(days=14), today)
        except (HttpError, BcchApiError, ValueError) as e:
            return HealthResult(False, str(e), "fail")
        if df.empty:
            return HealthResult(False, "token accepted but TPM returned no data (14d)", "fail")
        last = df.iloc[-1]
        return HealthResult(True, f"TPM {last.value:g}% on {last.obs_date}", "ok")
