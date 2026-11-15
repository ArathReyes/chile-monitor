"""INE Chile release calendar ("Agenda estadística", server-rendered HTML).

Page: https://www.ine.gob.cl/inicio/agendaestadistica (found via the INE homepage; no robots.txt
rules). Structure confirmed 2026-11-15 (fixture tests/fixtures/ine_agenda_2026-11-15.html):
  div.cuadroMes > h1.tituloMesEaV2 (month name, no year)
    div.eventoAgendaV2 > span.diaea2 (day), span.horaea2 (HH:MM, Santiago), span.tituloea2
      ("Índice de Precios al Consumidor (IPC), período noviembre 2026")
Months are listed in order starting from the current month; the year is inferred from that order.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd
from bs4 import BeautifulSoup

from monitor.http import HttpClient, HttpError
from monitor.parsing import MONTHS_ES
from monitor.sources.base import HealthResult, Source

AGENDA_URL = "https://www.ine.gob.cl/inicio/agendaestadistica"
# INE title prefix -> indicator key used across the app
INDICATORS = {
    "Índice de Precios al Consumidor (IPC)": "cpi",
    "Empleo Nacional": "unemployment",
    "Índice de Remuneraciones y Costos Laborales (IR-ICL)": "wages",
}
COLUMNS = ["indicator", "title", "reference_period", "release_local", "source", "source_url"]


def _reference_end(period: str) -> str | None:
    """'noviembre 2026' -> '2026-11'; 'agosto – octubre 2026' -> '2026-10' (moving quarter end)."""
    m = re.search(r"([a-záéíóú]+)\s+(\d{4})\s*$", period.strip().lower())
    if not m or m[1] not in MONTHS_ES:
        return None
    return f"{m[2]}-{MONTHS_ES[m[1]]:02d}"


def parse_agenda(html: str, today: date) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    blocks = soup.select("div.cuadroMes")
    if not blocks:
        raise ValueError("INE agenda: no month blocks (layout changed?)")
    rows, year, prev_month = [], today.year, None
    for blk in blocks:
        name = blk.select_one(".tituloMesEaV2").get_text(strip=True).lower()
        month = MONTHS_ES.get(name)
        if month is None:
            raise ValueError(f"INE agenda: unknown month {name!r}")
        if prev_month is not None and month < prev_month:
            year += 1
        prev_month = month
        for ev in blk.select("div.eventoAgendaV2"):
            day = int(re.sub(r"\D", "", ev.select_one(".diaea2").get_text()))
            hh, mm = re.search(r"(\d{1,2}):(\d{2})", ev.select_one(".horaea2").get_text()).groups()
            title = " ".join(ev.select_one(".tituloea2").get_text().split())
            name_part, _, period = title.partition(", período ")
            indicator = next((v for k, v in INDICATORS.items() if name_part.startswith(k)), "other")
            rows.append({
                "indicator": indicator, "title": title,
                "reference_period": _reference_end(period) if period else None,
                "release_local": datetime(year, month, day, int(hh), int(mm)),
                "source": "INE", "source_url": AGENDA_URL,
            })  # fmt: skip
    return pd.DataFrame(rows, columns=COLUMNS)


class IneSource(Source):
    name = "ine"

    def fetch_html(self) -> str:
        with HttpClient() as client:
            return client.get(AGENDA_URL).text

    def fetch(self, today: date | None = None) -> pd.DataFrame:
        return parse_agenda(self.fetch_html(), today or date.today())

    def health_check(self) -> HealthResult:
        try:
            df = self.fetch()
        except (HttpError, ValueError) as e:
            return HealthResult(False, str(e), "fail")
        nxt = df[(df.indicator == "cpi") & (df.release_local >= datetime.now())]
        msg = f"{len(df)} releases" + (
            f"; next CPI {nxt.release_local.min():%d %b %H:%M}" if len(nxt) else ""
        )
        return HealthResult(True, msg, "ok")
