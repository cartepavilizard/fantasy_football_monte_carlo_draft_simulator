# -*- coding: utf-8 -*-
"""
DATA SOURCE FOUNDATIONS (PHASE 0)

Shared plumbing for pulling player rankings/ADP and historical draft data
from external sources. Source-specific adapters (Sleeper, FFC, ESPN, ...)
are added in later phases; this package provides the pieces they all share:

- transport:  pluggable HTTP layer (httpx now, Playwright fallback later)
- ratelimit:  per-adapter request spacing
- cache:      on-disk raw-response cache so re-runs don't re-fetch
- base:       the adapter interface and the normalized record schema
- resolver:   canonical player-name resolution across sources
"""
from .base import BaseSourceAdapter, SourceFetchError, SourceRecord
from .cache import RawResponseCache
from .ratelimit import RateLimiter
from .resolver import PlayerResolver, Resolution, normalize_name
from .transport import HttpxTransport, PlaywrightTransport, TransportResponse

__all__ = [
    "BaseSourceAdapter",
    "HttpxTransport",
    "PlaywrightTransport",
    "PlayerResolver",
    "RateLimiter",
    "RawResponseCache",
    "Resolution",
    "SourceFetchError",
    "SourceRecord",
    "TransportResponse",
    "normalize_name",
]
