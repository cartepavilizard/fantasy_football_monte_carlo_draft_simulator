# -*- coding: utf-8 -*-
"""
ON-DISK RAW-RESPONSE CACHE

Stores each successful raw response keyed by (source, url, params) so that
re-running an ingest or blend within the TTL re-reads from disk instead of
re-hitting the source. Files are small JSON envelopes; the cache directory
is disposable at any time.
"""
import hashlib
import json
from pathlib import Path
import time
from typing import Optional

from .transport import TransportResponse


class RawResponseCache:
    def __init__(self, directory, ttl_seconds: float):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds

    def _path(self, source: str, url: str, params: Optional[dict]) -> Path:
        key = json.dumps([url, params or {}], sort_keys=True)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.directory / source / f"{digest}.json"

    def get(
        self, source: str, url: str, params: Optional[dict] = None
    ) -> Optional[TransportResponse]:
        """Return the cached response, or None if absent, stale, or corrupt"""
        path = self._path(source, url, params)
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - envelope["cached_at"] > self.ttl_seconds:
            return None
        return TransportResponse(
            status_code=envelope["status_code"],
            text=envelope["text"],
            url=envelope["url"],
        )

    def put(
        self,
        source: str,
        url: str,
        params: Optional[dict],
        response: TransportResponse,
    ):
        path = self._path(source, url, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "cached_at": time.time(),
            "status_code": response.status_code,
            "text": response.text,
            "url": response.url,
        }
        path.write_text(json.dumps(envelope), encoding="utf-8")
