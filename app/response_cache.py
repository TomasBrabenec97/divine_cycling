"""In-memory caches for the API's two large read-only payloads.

Building the PCS rider reference set or a finished leaderboard costs far more
CPU than any other request, and neither changes between one request and the
next: the reference data only moves when the PCS loader runs, and a published
leaderboard never moves at all. So each is rendered to JSON and gzipped once,
then served as bytes, with an ETag and a Cache-Control lifetime so browsers can
skip the request or revalidate it for free.
"""

from __future__ import annotations

import gzip
import hashlib
import threading
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class CachedJSON:
    body: bytes
    gzipped: bytes
    etag: str
    built_at: float = field(default_factory=time.monotonic)

    @classmethod
    def render(cls, content: Any) -> CachedJSON:
        # Going through JSONResponse keeps the bytes identical to what FastAPI
        # itself would have sent for the same content.
        body = JSONResponse(jsonable_encoder(content)).body
        return cls(
            body=body,
            gzipped=gzip.compress(body),
            etag=f'"{hashlib.sha256(body).hexdigest()[:32]}"',
        )

    def response(self, request: Request, max_age: int) -> Response:
        headers = {
            "ETag": self.etag,
            "Cache-Control": f"public, max-age={max_age}",
            "Vary": "Accept-Encoding",
        }
        if self.etag in request.headers.get("if-none-match", ""):
            return Response(status_code=304, headers=headers)
        # GZipMiddleware leaves a response alone once it carries a Content-Encoding.
        if "gzip" in request.headers.get("accept-encoding", "").lower():
            headers["Content-Encoding"] = "gzip"
            return Response(self.gzipped, media_type="application/json", headers=headers)
        return Response(self.body, media_type="application/json", headers=headers)


class ResponseCache:
    """One rendered payload per key, rebuilt when its version changes or it ages out.

    Requests that miss together wait for a single build instead of each
    rebuilding the payload, which is what would saturate a small instance.
    """

    def __init__(self, ttl_seconds: float | None = None) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[Hashable, tuple[Hashable, CachedJSON]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable, version: Hashable, build: Callable[[], Any]) -> CachedJSON:
        entry = self._fresh(key, version)
        if entry is None:
            with self._lock:
                entry = self._fresh(key, version)
                if entry is None:
                    entry = CachedJSON.render(build())
                    self._entries[key] = (version, entry)
        return entry

    def _fresh(self, key: Hashable, version: Hashable) -> CachedJSON | None:
        stored = self._entries.get(key)
        if stored is None or stored[0] != version:
            return None
        entry = stored[1]
        if self.ttl_seconds is not None and time.monotonic() - entry.built_at > self.ttl_seconds:
            return None
        return entry

    def clear(self) -> None:
        self._entries.clear()
