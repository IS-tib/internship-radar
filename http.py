"""
HTTP layer: retries with backoff, per-host rate limiting, and honest error types.

The previous implementation used a bare urllib call with no retry and no pacing.
A single transient 502 permanently dropped a company from that run, and hammering
one ATS with 100 concurrent requests is both rude and a good way to get throttled.

Standard library only, so the scraper still runs anywhere with no install step.
"""

from __future__ import annotations

import gzip
import json
import random
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from urllib.parse import urlparse

USER_AGENT = (
    "internship-radar/3.0 "
    "(+https://github.com/IS-tib/internship-radar; job-board aggregator; contact via repo issues)"
)

DEFAULT_TIMEOUT = 25
DEFAULT_RETRIES = 3

#: Minimum seconds between requests to the same host, so we stay polite even
#: when the thread pool wants to fire 16 requests at one ATS simultaneously.
HOST_MIN_INTERVAL = 0.35


class FetchError(Exception):
    """Base for fetch failures, carrying enough context to record source health."""

    def __init__(self, message, *, status=None, url=""):
        super().__init__(message)
        self.status = status
        self.url = url


class NotFound(FetchError):
    """404/410 — almost always a dead or renamed board token."""


class RateLimited(FetchError):
    """429 or 403-with-backoff semantics."""


class ServerError(FetchError):
    """5xx — transient, worth retrying."""


class _HostThrottle:
    """Serialises requests per host to a minimum interval."""

    def __init__(self, interval=HOST_MIN_INTERVAL):
        self.interval = interval
        self._last = defaultdict(float)
        self._locks = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def _lock_for(self, host):
        with self._guard:
            return self._locks[host]

    def wait(self, url):
        host = urlparse(url).netloc
        lock = self._lock_for(host)
        with lock:
            now = time.monotonic()
            delta = now - self._last[host]
            if delta < self.interval:
                time.sleep(self.interval - delta)
            self._last[host] = time.monotonic()


_throttle = _HostThrottle()


def _read(resp) -> str:
    data = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    charset = resp.headers.get_content_charset() or "utf-8"
    return data.decode(charset, "replace")


def request(url, *, data=None, headers=None, timeout=DEFAULT_TIMEOUT,
            retries=DEFAULT_RETRIES, method=None) -> str:
    """Fetch a URL, retrying transient failures with exponential backoff + jitter.

    Raises NotFound / RateLimited / ServerError / FetchError so the caller can
    record *why* a source failed rather than swallowing every exception alike.
    """
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip",
    }
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)

    body = json.dumps(data).encode("utf-8") if data is not None else None
    last = None

    for attempt in range(retries):
        _throttle.wait(url)
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _read(resp)
        except urllib.error.HTTPError as e:
            status = e.code
            if status in (404, 410):
                raise NotFound(f"HTTP {status}", status=status, url=url) from e
            if status in (429,):
                last = RateLimited(f"HTTP {status}", status=status, url=url)
            elif 500 <= status < 600:
                last = ServerError(f"HTTP {status}", status=status, url=url)
            else:
                # 401/403 and friends: not worth retrying, the board is closed to us.
                raise FetchError(f"HTTP {status}", status=status, url=url) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = FetchError(f"{type(e).__name__}: {e}", url=url)

        if attempt < retries - 1:
            backoff = (2 ** attempt) + random.uniform(0, 0.4)
            time.sleep(backoff)

    raise last or FetchError("exhausted retries", url=url)


def get_json(url, **kw):
    """GET a URL and parse JSON, raising FetchError on malformed payloads."""
    text = request(url, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise FetchError(f"invalid JSON ({e})", url=url) from e


def post_json(url, payload, **kw):
    text = request(url, data=payload, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise FetchError(f"invalid JSON ({e})", url=url) from e


def get_text(url, **kw):
    return request(url, **kw)
