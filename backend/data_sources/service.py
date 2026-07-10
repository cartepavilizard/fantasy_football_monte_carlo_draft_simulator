# -*- coding: utf-8 -*-
"""
RANKINGS REFRESH ORCHESTRATION

One entry point (refresh_rankings) fetches every configured source,
resolves player names against an anchor namespace, persists the raw
batches, and regenerates the blend. Sources fail independently: a broken
source is recorded as a success=False batch, and the blend falls back to
that source's LAST SUCCESSFUL batch if one exists — last-known-good
beats a hole in the blend during draft prep, and the staleness is
surfaced (not hidden) by source_status / GET /rankings/status.

Push sources (the UDK file drop) never fetch; their latest uploaded batch
joins every blend the same way.

Canonical naming: the league's drafts live on ESPN, and Phase 3's
historical picks will be resolved against league player pools built here
— so ESPN's spellings anchor the namespace whenever ESPN succeeds, with
Sleeper then FFC as fallbacks.
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from odmantic import query

from models.config import (
    DATA_SOURCE_CACHE_DIR,
    DATA_SOURCE_CACHE_TTL_SECONDS,
    FANTASYPROS_API_KEY,
    RANKING_BLEND_WEIGHTS,
    YAHOO_CLIENT_ID,
    YAHOO_CLIENT_SECRET,
    YAHOO_REFRESH_TOKEN,
)
from models.sources import BlendedRanking, SourceRankingBatch, SourceRankingRecord

from .base import BaseSourceAdapter
from .blend import blend_batches
from .cache import RawResponseCache
from .espn_rankings import EspnRankingsAdapter
from .fantasypros import FantasyProsAdapter
from .ffc import FantasyFootballCalculatorAdapter
from .resolver import PlayerResolver, load_alias_overrides
from .sleeper import SleeperAdapter
from .yahoo import YahooAdapter

ADAPTER_CLASSES = {
    "sleeper": SleeperAdapter,
    "ffc": FantasyFootballCalculatorAdapter,
    "espn": EspnRankingsAdapter,
    "fantasypros": FantasyProsAdapter,
    "yahoo": YahooAdapter,
}

# Sources that are uploaded (file drop), never fetched
PUSH_SOURCES = ["udk"]

ALL_SOURCES = list(ADAPTER_CLASSES) + PUSH_SOURCES

# Whose spellings become canonical, in order of preference
ANCHOR_PRIORITY = ["espn", "sleeper", "ffc"]


def _yahoo_configured() -> bool:
    return bool(YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET and YAHOO_REFRESH_TOKEN)


def default_sources() -> List[str]:
    """Every pull source that can plausibly succeed with current config"""
    return [
        name
        for name in ADAPTER_CLASSES
        if name != "yahoo" or _yahoo_configured()
    ]


def build_adapters(
    sources: Optional[List[str]] = None,
) -> Dict[str, BaseSourceAdapter]:
    """Instantiate the requested adapters with a shared raw-response cache"""
    names = sources or default_sources()
    unknown = sorted(set(names) - set(ADAPTER_CLASSES))
    if unknown:
        raise ValueError(
            f"Unknown ranking sources {unknown}; "
            f"available: {sorted(ADAPTER_CLASSES)} "
            f"(plus push-only: {PUSH_SOURCES})"
        )
    cache = RawResponseCache(DATA_SOURCE_CACHE_DIR, DATA_SOURCE_CACHE_TTL_SECONDS)
    return {name: ADAPTER_CLASSES[name](cache=cache) for name in names}


async def latest_batch(
    engine,
    source: str,
    season: int,
    scoring_format: str,
    successful_only: bool = True,
) -> Optional[SourceRankingBatch]:
    """The most recent (successful) stored batch for one source"""
    criteria = (
        (SourceRankingBatch.source == source)
        & (SourceRankingBatch.season == season)
        & (SourceRankingBatch.scoring_format == scoring_format)
    )
    if successful_only:
        criteria = criteria & (SourceRankingBatch.success == True)  # noqa: E712
    return await engine.find_one(
        SourceRankingBatch,
        criteria,
        # fetched_at is ms-truncated, so batches stored back-to-back tie;
        # id (monotonic within a process) breaks toward the newest
        sort=(
            query.desc(SourceRankingBatch.fetched_at),
            query.desc(SourceRankingBatch.id),
        ),
    )


def build_anchor_resolver(
    batches: List[SourceRankingBatch], overrides: dict
) -> Optional[PlayerResolver]:
    """Seed a resolver from the highest-priority successful batch"""
    by_source = {batch.source: batch for batch in batches}
    for source in ANCHOR_PRIORITY + sorted(set(by_source) - set(ANCHOR_PRIORITY)):
        batch = by_source.get(source)
        if batch and batch.success and batch.records:
            pool = [
                # Anchor batches self-resolve, so canonical==raw; prefer
                # canonical in case a stored batch is reused as anchor
                (
                    record.canonical_name or record.raw_name,
                    record.position,
                    record.nfl_team,
                )
                for record in batch.records
            ]
            return PlayerResolver(pool, overrides=overrides)
    return None


async def stored_anchor_resolver(
    engine, season: int, scoring_format: str
) -> Optional[PlayerResolver]:
    """Anchor resolver built from the store (for push-source ingestion)"""
    overrides = await load_alias_overrides(engine)
    for source in ANCHOR_PRIORITY:
        batch = await latest_batch(engine, source, season, scoring_format)
        if batch and batch.records:
            return build_anchor_resolver([batch], overrides)
    return None


def resolve_batch(batch: SourceRankingBatch, resolver: PlayerResolver):
    """Fill canonical names on a batch's records in place"""
    resolved_records = []
    for record in batch.records:
        resolution = resolver.resolve(
            record.raw_name, position=record.position, nfl_team=record.nfl_team
        )
        data = record.model_dump()
        data["canonical_name"] = resolution.canonical_name
        data["resolution_method"] = resolution.method
        data["resolution_confidence"] = resolution.confidence
        resolved_records.append(SourceRankingRecord(**data))
    batch.records = resolved_records


def _batch_stats(batch: SourceRankingBatch) -> dict:
    resolved = sum(
        1 for record in batch.records if record.canonical_name is not None
    )
    return {
        "success": batch.success,
        "error": batch.error,
        "records": len(batch.records),
        "resolved": resolved,
        "unresolved": len(batch.records) - resolved,
        "fetched_at": batch.fetched_at.isoformat(),
    }


async def rebuild_blend(
    engine,
    season: int,
    scoring_format: str,
    weights: Optional[Dict[str, float]] = None,
) -> BlendedRanking:
    """
    Blend the latest successful stored batch of every source (pull and
    push) and persist the result
    """
    batches = []
    for source in ALL_SOURCES:
        batch = await latest_batch(engine, source, season, scoring_format)
        if batch:
            batches.append(batch)
    blend = blend_batches(
        batches,
        season=season,
        scoring_format=scoring_format,
        weights=weights if weights is not None else RANKING_BLEND_WEIGHTS,
    )
    await engine.save(blend)
    return blend


async def refresh_rankings(
    engine,
    season: int,
    scoring_format: str,
    sources: Optional[List[str]] = None,
    adapters: Optional[Dict[str, BaseSourceAdapter]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> dict:
    """
    Fetch all (requested) pull sources, resolve, persist batches, then
    regenerate the blend from every source's last-known-good batch
    """
    if adapters is None:
        adapters = build_adapters(sources)
    batches = await asyncio.gather(
        *(
            adapter.fetch_batch(season, scoring_format)
            for adapter in adapters.values()
        )
    )

    overrides = await load_alias_overrides(engine)
    resolver = build_anchor_resolver(batches, overrides)
    if resolver is not None:
        for batch in batches:
            if batch.success:
                resolve_batch(batch, resolver)

    summary_sources = {}
    for batch in batches:
        await engine.save(batch)
        summary_sources[batch.source] = _batch_stats(batch)

    blend = await rebuild_blend(engine, season, scoring_format, weights=weights)

    return {
        "season": season,
        "scoring_format": scoring_format,
        "sources": summary_sources,
        "blend": {
            "id": str(blend.id),
            "sources_used": blend.sources_used,
            "records": len(blend.records),
        },
    }


async def ingest_push_batch(
    engine, batch: SourceRankingBatch, weights: Optional[Dict[str, float]] = None
) -> dict:
    """
    Store an uploaded (push-source) batch — resolving names against the
    stored anchor namespace — and regenerate the blend to include it
    """
    resolver = await stored_anchor_resolver(
        engine, batch.season, batch.scoring_format
    )
    if resolver is not None:
        resolve_batch(batch, resolver)
    await engine.save(batch)
    blend = await rebuild_blend(
        engine, batch.season, batch.scoring_format, weights=weights
    )
    stats = _batch_stats(batch)
    stats["anchored"] = resolver is not None
    return {
        "season": batch.season,
        "scoring_format": batch.scoring_format,
        "source": batch.source,
        "batch": stats,
        "blend": {
            "id": str(blend.id),
            "sources_used": blend.sources_used,
            "records": len(blend.records),
        },
    }


async def source_status(engine, season: int, scoring_format: str) -> dict:
    """
    Per-source freshness: last attempt, last success, and staleness —
    the alert surface for silently degrading sources
    """
    now = datetime.now()
    sources = {}
    for source in ALL_SOURCES:
        last_attempt = await latest_batch(
            engine, source, season, scoring_format, successful_only=False
        )
        last_success = (
            last_attempt
            if last_attempt is not None and last_attempt.success
            else await latest_batch(engine, source, season, scoring_format)
        )
        entry = {
            "kind": "push" if source in PUSH_SOURCES else "pull",
            "configured": True,
            "last_attempt": _batch_stats(last_attempt) if last_attempt else None,
            "last_success": _batch_stats(last_success) if last_success else None,
            "age_seconds": (
                round((now - last_success.fetched_at).total_seconds())
                if last_success
                else None
            ),
        }
        if source == "yahoo":
            entry["configured"] = _yahoo_configured()
        if source == "fantasypros":
            entry["access_mode"] = "api" if FANTASYPROS_API_KEY else "page"
        sources[source] = entry

    blend = await engine.find_one(
        BlendedRanking,
        (BlendedRanking.season == season)
        & (BlendedRanking.scoring_format == scoring_format),
        # ms-truncated timestamps tie for back-to-back blends; id breaks
        # toward the newest
        sort=(query.desc(BlendedRanking.generated_at), query.desc(BlendedRanking.id)),
    )
    return {
        "season": season,
        "scoring_format": scoring_format,
        "sources": sources,
        "blend_weights": RANKING_BLEND_WEIGHTS,
        "blend": (
            {
                "generated_at": blend.generated_at.isoformat(),
                "sources_used": blend.sources_used,
                "records": len(blend.records),
            }
            if blend
            else None
        ),
    }
