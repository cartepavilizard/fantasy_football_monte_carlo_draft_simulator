# -*- coding: utf-8 -*-
"""
SOURCE ADAPTER INTERFACE

Each ranking/ADP source gets one adapter subclass. Adapters translate a
source's own shape into SourceRecord rows; everything else — transport,
rate limiting, caching, name resolution, persistence shape — is shared
here so no source-specific quirk leaks past its adapter.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from .cache import RawResponseCache
from .ratelimit import RateLimiter
from .resolver import PlayerResolver
from .transport import HttpxTransport, Transport, TransportResponse


class SourceFetchError(RuntimeError):
    """A source could not be fetched or parsed; the batch records the reason"""


class SourceRecord(BaseModel):
    """One player row in a source's own terms, before name resolution"""

    raw_name: str
    position: str
    nfl_team: Optional[str] = None
    rank: Optional[float] = None
    position_rank: Optional[float] = None
    tier: Optional[int] = None
    adp: Optional[float] = None
    projection: Optional[float] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class BaseSourceAdapter(ABC):
    """
    Subclasses set source_name, tune the class knobs if needed, and
    implement fetch(). Use self._get() for every request so rate limiting
    and caching apply uniformly.
    """

    source_name: ClassVar[str]
    min_request_interval_seconds: ClassVar[float] = 1.0

    def __init__(
        self,
        transport: Optional[Transport] = None,
        cache: Optional[RawResponseCache] = None,
    ):
        self.transport = transport or HttpxTransport()
        self.cache = cache  # None disables raw-response caching
        self._rate_limiter = RateLimiter(self.min_request_interval_seconds)

    @abstractmethod
    async def fetch(self, season: int, scoring_format: str) -> List[SourceRecord]:
        """Pull and parse the source into SourceRecord rows"""

    async def _get(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        use_cache: bool = True,
    ) -> TransportResponse:
        if self.cache and use_cache:
            cached = self.cache.get(self.source_name, url, params)
            if cached is not None:
                return cached
        await self._rate_limiter.wait()
        response = await self.transport.get(url, params=params, headers=headers)
        if not response.ok:
            raise SourceFetchError(
                f"{self.source_name}: GET {url} returned {response.status_code}"
            )
        if self.cache and use_cache:
            self.cache.put(self.source_name, url, params, response)
        return response

    async def fetch_batch(
        self,
        season: int,
        scoring_format: str,
        resolver: Optional[PlayerResolver] = None,
    ):
        """
        Fetch and package one SourceRankingBatch document (not yet saved).
        A failing source produces a success=False batch instead of raising,
        so one broken source degrades the blend rather than breaking it.
        """
        from models.sources import SourceRankingBatch, SourceRankingRecord

        try:
            records = await self.fetch(season, scoring_format)
        except Exception as exc:
            return SourceRankingBatch(
                source=self.source_name,
                season=season,
                scoring_format=scoring_format,
                fetched_at=datetime.now(),
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                records=[],
            )

        batch_records = []
        for record in records:
            canonical_name, method, confidence = None, "unresolved", 0.0
            if resolver is not None:
                resolution = resolver.resolve(
                    record.raw_name,
                    position=record.position,
                    nfl_team=record.nfl_team,
                )
                canonical_name = resolution.canonical_name
                method = resolution.method
                confidence = resolution.confidence
            batch_records.append(
                SourceRankingRecord(
                    raw_name=record.raw_name,
                    canonical_name=canonical_name,
                    resolution_method=method,
                    resolution_confidence=confidence,
                    position=record.position,
                    nfl_team=record.nfl_team,
                    rank=record.rank,
                    position_rank=record.position_rank,
                    tier=record.tier,
                    adp=record.adp,
                    projection=record.projection,
                )
            )
        return SourceRankingBatch(
            source=self.source_name,
            season=season,
            scoring_format=scoring_format,
            fetched_at=datetime.now(),
            success=True,
            records=batch_records,
        )
