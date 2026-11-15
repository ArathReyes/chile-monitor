"""Pydantic models and loaders for config/*.yaml and .env secrets."""

from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"

Freq = Literal["D", "W", "M", "Q", "A"]


class SeriesSpec(BaseModel):
    series_id: str
    source: str
    source_code: str
    name: str
    freq: Freq
    unit: str
    tab: str
    transform: str = "level"
    verified: bool = False
    notes: str | None = None

    @property
    def is_placeholder(self) -> bool:
        """True when the source code is still the literal VERIFY marker."""
        return self.source_code.strip().upper() == "VERIFY"


class SeriesRegistry(BaseModel):
    series: list[SeriesSpec]

    def by_id(self, series_id: str) -> SeriesSpec:
        for s in self.series:
            if s.series_id == series_id:
                return s
        raise KeyError(series_id)


class SourceDefaults(BaseModel):
    timeout_s: float = 20.0
    min_interval_s: float = 1.0
    retries: int = 3
    user_agent: str


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    base_url: str | None = None
    env_key: str | None = None
    min_interval_s: float | None = None

    @property
    def base_url_verified(self) -> bool:
        return bool(self.base_url) and self.base_url.strip().upper() != "VERIFY"


class SourcesConfig(BaseModel):
    defaults: SourceDefaults
    sources: dict[str, SourceSpec]


class Meeting(BaseModel):
    meeting_id: str
    decision_date: date
    start_date: date | None = None
    decision_time_local: str | None = None
    effective_date: date | None = None
    has_ipom: bool = False
    source: str = ""
    confirmed: bool = False

    @property
    def effective(self) -> date:
        """Effective date: explicit, else the next Chilean business day after the decision."""
        from monitor.cl_calendar import next_business_day

        return self.effective_date or next_business_day(self.decision_date)


class MeetingCalendar(BaseModel):
    timezone: str
    meetings: list[Meeting] = Field(default_factory=list)


class MeetingsConfig(BaseModel):
    bcch: MeetingCalendar
    fomc: MeetingCalendar


class ThemeIndicator(BaseModel):
    source: str
    code: str
    label: str


class Theme(BaseModel):
    id: str
    title: str
    thesis: str
    indicators: list[ThemeIndicator] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache
def load_series(path: Path = CONFIG_DIR / "series.yaml") -> SeriesRegistry:
    return SeriesRegistry.model_validate(_read_yaml(path))


@lru_cache
def load_sources(path: Path = CONFIG_DIR / "sources.yaml") -> SourcesConfig:
    return SourcesConfig.model_validate(_read_yaml(path))


@lru_cache
def load_meetings(path: Path = CONFIG_DIR / "meetings.yaml") -> MeetingsConfig:
    return MeetingsConfig.model_validate(_read_yaml(path))


def load_themes(directory: Path = CONFIG_DIR / "themes") -> list[Theme]:
    return [Theme.model_validate(_read_yaml(p)) for p in sorted(directory.glob("*.yaml"))]


_ENV_LOADED = False


def get_secret(name: str) -> str | None:
    """Return a secret from .env / environment, or None if unset or blank. Never log the value."""
    global _ENV_LOADED
    if not _ENV_LOADED:
        load_dotenv(ROOT / ".env", override=False)
        _ENV_LOADED = True
    value = os.environ.get(name, "").strip()
    return value or None
