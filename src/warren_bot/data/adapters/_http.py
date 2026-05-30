"""Small shared JSON-over-HTTP helper for the network adapters.

Keeps headers, timeouts, throttling and retry/backoff in one place so EDGAR, FMP
and Finnhub behave consistently and politely. Uses urllib (stdlib) to avoid a new
dependency; the volume here is tiny (dozens of calls per run, on finalists only).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

log = logging.getLogger(__name__)


class RateLimiter:
    """Thread-safe minimum-interval gate (mirrors fetcher.RateLimiter)."""

    def __init__(self, requests_per_sec: float):
        self.min_interval = 1.0 / requests_per_sec if requests_per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self.min_interval


def _redact(url: str) -> str:
    """Drop the query string before logging — it can carry API keys/tokens
    (e.g. FMP's ``?apikey=…``). The path alone is enough to identify the call."""
    base, sep, _ = url.partition("?")
    return base + "?…" if sep else base


def get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: float = 20.0, retries: int = 2, backoff: float = 1.5,
             limiter: RateLimiter | None = None):
    """GET ``url`` and parse JSON. Returns the decoded object, or None on a
    persistent failure / 404. Retries transient network errors with backoff.
    HTTP 4xx other than 429 are treated as 'no data' (return None) rather than
    retried, since they won't fix themselves."""
    if params:
        url = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(url, headers=headers or {})
    for attempt in range(retries + 1):
        if limiter is not None:
            limiter.wait()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code != 429 and 400 <= e.code < 500:
                log.debug("HTTP %s for %s (not retrying)", e.code, _redact(url))
                return None
            log.debug("HTTP %s for %s (attempt %d)", e.code, _redact(url), attempt + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            log.debug("network error for %s: %s (attempt %d)", _redact(url), e, attempt + 1)
        except (ValueError, json.JSONDecodeError) as e:
            log.debug("bad JSON from %s: %s", _redact(url), e)
            return None
        if attempt < retries:
            time.sleep(backoff * (2 ** attempt))
    return None
