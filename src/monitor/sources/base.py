"""Source protocol: every data source exposes ``name``, ``fetch`` and ``health_check``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd

from monitor.config import SourceSpec, get_secret, load_sources


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    message: str
    status: str = ""  # "ok" | "fail" | "no_key" | "stub"

    def __bool__(self) -> bool:
        return self.ok


class Source(ABC):
    """Base class. Subclasses set ``name`` (key in config/sources.yaml)."""

    name: str

    @property
    def spec(self) -> SourceSpec:
        return load_sources().sources[self.name]

    @property
    def env_key(self) -> str | None:
        return self.spec.env_key

    def secret(self) -> str | None:
        return get_secret(self.env_key) if self.env_key else None

    def missing_key(self) -> HealthResult | None:
        """Return a no_key HealthResult if this source needs a key that is not set."""
        if self.env_key and not self.secret():
            return HealthResult(False, f"{self.env_key} not set in .env", "no_key")
        return None

    @abstractmethod
    def fetch(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        """Return a tidy DataFrame ``[series_id, obs_date, value]`` (or a typed record list)."""

    @abstractmethod
    def health_check(self) -> HealthResult: ...


class StubSource(Source):
    """Placeholder for sources not implemented yet."""

    def fetch(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        raise NotImplementedError(f"{self.name}: not implemented yet, see docs/specs/")

    def health_check(self) -> HealthResult:
        return HealthResult(False, "stub, not implemented yet", "stub")
