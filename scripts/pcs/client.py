"""Polite, cached HTTP access to ProCyclingStats.

Every PCS request in this repository goes through one client so the courtesy
rules live in a single place: a fixed pause between network hits, exponential
backoff when PCS throttles us, and an on-disk cache of every page already
seen. Re-running a fetcher therefore costs no requests at all unless something
genuinely new is asked for, which is what keeps repeated runs cheap for PCS.

PCS disallows automated access in robots.txt, so run these scripts from your
own machine at human pace -- never from a server, a cron job or CI -- and
please leave the delay in.
"""

from __future__ import annotations

import hashlib
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import requests

BASE_URL = "https://www.procyclingstats.com/"
DEFAULT_CACHE_DIR = Path(".cache/pcs")
DEFAULT_DELAY = 2.0
DEFAULT_RETRIES = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# PCS answers a race edition that never existed with 500 and a one-byte body
# rather than 404, so a tiny error body means "not here", not "try again".
TINY_BODY_BYTES = 200

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class PCSUnavailable(RuntimeError):
    """PCS answered, but the requested page does not exist."""


@dataclass
class PCSClient:
    """Fetch PCS pages one at a time, remembering everything on disk."""

    cache_dir: Path = DEFAULT_CACHE_DIR
    delay: float = DEFAULT_DELAY
    retries: int = DEFAULT_RETRIES
    refresh: bool = False
    verbose: bool = True
    session: requests.Session = field(default_factory=requests.Session)
    stats: Counter = field(default_factory=Counter)
    _last_request_at: float = field(default=0.0, init=False)

    def url(self, path: str, params: dict[str, object] | None = None) -> str:
        url = BASE_URL + path.lstrip("/")
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        return url

    def get(self, path: str, params: dict[str, object] | None = None) -> str:
        """Return the page body, from cache when we have already seen it."""
        url = self.url(path, params)
        cached = self._read_cache(url)
        if cached is not None:
            self.stats["cache"] += 1
            return cached

        html = self._download(url)
        self._write_cache(url, html)
        self.stats["network"] += 1
        return html

    def get_bytes(self, path: str, referer: str = "") -> bytes:
        """Return a binary asset such as an image, cached on disk like pages are.

        PCS refuses images requested without the page that shows them, so pass
        that page's path as ``referer``.
        """
        url = self.url(path)
        cached = self._cache_path(url).with_suffix(".bin")
        if not self.refresh and cached.exists():
            self.stats["cache"] += 1
            return cached.read_bytes()

        headers = dict(HEADERS, Accept="image/avif,image/webp,image/png,image/*,*/*;q=0.8")
        if referer:
            headers["Referer"] = self.url(referer)
        self._wait_turn()
        response = self.session.get(url, headers=headers, timeout=30)
        self._last_request_at = time.monotonic()
        if response.status_code == 404:
            raise PCSUnavailable(f"404 {url}")
        response.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)
        self.stats["network"] += 1
        return response.content

    def get_optional(self, path: str, params: dict[str, object] | None = None) -> str | None:
        """Like :meth:`get`, but return ``None`` instead of raising on 404/500."""
        try:
            return self.get(path, params)
        except PCSUnavailable as error:
            self._log(f"    unavailable: {error}")
            self.stats["missing"] += 1
            return None

    # -- internals ---------------------------------------------------------

    def _download(self, url: str) -> str:
        for attempt in range(1, self.retries + 1):
            self._wait_turn()
            response = self.session.get(url, headers=HEADERS, timeout=30)
            self._last_request_at = time.monotonic()

            if response.status_code == 404:
                raise PCSUnavailable(f"404 {url}")
            # A short 5xx body is PCS's way of saying the edition does not
            # exist; a long one is a real outage worth retrying.
            if response.status_code >= 500 and len(response.content) < TINY_BODY_BYTES:
                raise PCSUnavailable(f"{response.status_code} (empty) {url}")
            if response.status_code not in (429, 500, 502, 503, 504):
                response.raise_for_status()
                return response.text

            wait = self.delay * 2 ** (attempt + 1)
            self._log(
                f"    HTTP {response.status_code}, retrying in {wait:.0f}s "
                f"({attempt}/{self.retries})"
            )
            time.sleep(wait)

        raise PCSUnavailable(f"gave up after {self.retries} attempts: {url}")

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        stem = _UNSAFE.sub("-", url[len(BASE_URL) :]).strip("-")[:80] or "index"
        return self.cache_dir / f"{stem}__{digest}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if self.refresh or not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def _write_cache(self, url: str, html: str) -> None:
        path = self._cache_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)

    def summary(self) -> str:
        return (
            f"{self.stats['network']} downloaded, {self.stats['cache']} from cache, "
            f"{self.stats['missing']} unavailable"
        )
