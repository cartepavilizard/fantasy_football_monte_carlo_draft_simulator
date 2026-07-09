# -*- coding: utf-8 -*-
"""
RANKINGS REFRESH ORCHESTRATION

One entry point (refresh_rankings) fetches every configured source,
resolves player names against an anchor namespace, persists the raw
batches, and generates + persists the blend. Sources fail independently:
a broken source is recorded as a success=False batch and simply drops out
of the blend.

Canonical naming: the league's drafts live on ESPN, and Phase 3's
historical picks will be resolved against league player pools built here
— so ESPN's spellings anchor the namespace whenever ESPN succeeds, with
Sleeper then FFC as fallbacks.
"""
import asyncio
from typing import Dict, List, Optional

from models.config import (
    DATA_SOURCE_CACHE_DIR,
    DATA_SOURCE_CACHE_TTL_SECONDS,
    RANKING_BLEND_WEIGHTS,
)
from models.sources import SourceRankingBatch, SourceRankingRecord

from .base import BaseSourceAdapter
from .blend import blend_batches
from .cache import RawResponseCache
from .espn_rankings import EspnRankingsAdapter
from .ffc import FantasyFootballCalculatorAdapter
from .resolver import PlayerResolver, load_alias_overrides
from .sleeper import SleeperAdapter

ADAPTER_CLASSES = {
    "sleeper": SleeperAdapter,
    "ffc": FantasyFootballCalculatorAdapter,
    "espn": EspnRankingsAdapter,
}

# Whose spellings become canonical, in order of preference
ANCHOR_PRIORITY = ["espn", "sleeper", "ffc"]


def build_adapters(
    sources: Optional[List[str]] = None,
) -> Dict[str, BaseSourceAdapter]:
    """Instantiate the requested adapters with a shared raw-response cache"""
    names = sources or list(ADAPTER_CLASSES)
    unknown = sorted(set(names) - set(ADAPTER_CLASSES))
    if unknown:
        raise ValueError(
            f"Unknown ranking sources {unknown}; "
            f"available: {sorted(ADAPTER_CLASSES)}"
        )
    cache = RawResponseCache(DATA_SOURCE_CACHE_DIR, DATA_SOURCE_CACHE_TTL_SECONDS)
    return {name: ADAPTER_CLASSES[name](cache=cache) for name in names}


def build_anchor_resolver(
    batches: List[SourceRankingBatch], overrides: dict
) -> Optional[PlayerResolver]:
    """Seed a resolver from the highest-priority successful batch"""
    by_source = {batch.source: batch for batch in batches}
    for source in ANCHOR_PRIORITY + sorted(set(by_source) - set(ANCHOR_PRIORITY)):
        batch = by_source.get(source)
        if batch and batch.success and batch.records:
            pool = [
                (record.raw_name, record.position, record.nfl_team)
                for record in batch.records
            ]
            return PlayerResolver(pool, overrides=overrides)
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


async def refresh_rankings(
    engine,
    season: int,
    scoring_format: str,
    sources: Optional[List[str]] = None,
    adapters: Optional[Dict[str, BaseSourceAdapter]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> dict:
    """
    Fetch all (requested) sources, resolve, persist batches, blend,
    persist the blend, and return a per-source summary
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
        resolved = sum(
            1 for record in batch.records if record.canonical_name is not None
        )
        summary_sources[batch.source] = {
            "success": batch.success,
            "error": batch.error,
            "records": len(batch.records),
            "resolved": resolved,
            "unresolved": len(batch.records) - resolved,
        }

    blend = blend_batches(
        batches,
        season=season,
        scoring_format=scoring_format,
        weights=weights if weights is not None else RANKING_BLEND_WEIGHTS,
    )
    await engine.save(blend)

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
