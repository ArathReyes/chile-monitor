"""Shared HTTP client: timeouts, retries with backoff, User-Agent, per-host rate limit.

Secrets (e.g. the BCCh token) can travel in query strings, so every message that may contain
a URL goes through ``redact`` and httpx's own request logging is silenced.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from monitor.config import load_sources

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs at INFO

_SECRET_PARAM = re.compile(r"(?i)\b(token|api_key|apikey|key|pass|password)=([^&\s'\"]+)")


def redact(text: str) -> str:
    """Mask secret-looking query parameters in a string."""
    return _SECRET_PARAM.sub(r"\1=***", text)


class HttpError(RuntimeError):
    """Raised when a request ultimately fails. The message is always redacted."""


class _HostRateLimiter:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, min_interval_s: float) -> None:
        with self._lock:
            now = time.monotonic()
            ready_at = self._last.get(host, 0.0) + min_interval_s
            delay = max(0.0, ready_at - now)
            self._last[host] = now + delay
        if delay:
            time.sleep(delay)


_limiter = _HostRateLimiter()


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


class HttpClient:
    """Thin wrapper over httpx.Client. Use as a context manager."""

    def __init__(
        self,
        min_interval_s: float | None = None,
        timeout_s: float | None = None,
        retries: int | None = None,
    ) -> None:
        d = load_sources().defaults
        self.min_interval_s = d.min_interval_s if min_interval_s is None else min_interval_s
        self.retries = d.retries if retries is None else retries
        self._client = httpx.Client(
            timeout=d.timeout_s if timeout_s is None else timeout_s,
            headers={"User-Agent": d.user_agent},
            follow_redirects=True,
        )

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """GET with rate limit and retries; raises HttpError (redacted) on final failure."""
        host = urlsplit(url).netloc

        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.retries),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            reraise=True,
        )
        def _do() -> httpx.Response:
            _limiter.wait(host, self.min_interval_s)
            resp = self._client.get(url, **kwargs)
            resp.raise_for_status()
            return resp

        try:
            return _do()
        except httpx.HTTPStatusError as e:
            raise HttpError(redact(f"HTTP {e.response.status_code} from {host}")) from None
        except httpx.HTTPError as e:
            raise HttpError(redact(f"{type(e).__name__} contacting {host}: {e}")) from None
