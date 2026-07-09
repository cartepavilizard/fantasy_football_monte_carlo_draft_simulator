# -*- coding: utf-8 -*-
"""
CROSS-SOURCE BLENDING

Different sources speak different value languages: projections (points),
ADP (picks), draft ranks (ordinals). To combine them sanely, each source's
values are converted to z-scores WITHIN each position (per source), then
blended as a weighted average. A rank-heavy source and a points-heavy
source therefore contribute comparable signals, and cross-position scale
differences never leak between sources.

Only resolved records (canonical_name set) participate; unresolved rows
stay in their SourceRankingBatch for review and are counted, not guessed.
"""
from statistics import fmean, pstdev
from typing import Dict, List, Optional

from models.sources import BlendedRanking, BlendedRankingRecord, SourceRankingBatch

from .resolver import normalize_position

BLENDABLE_POSITIONS = {"qb", "rb", "wr", "te", "dst", "k"}

# Per (source, position) group, the first metric at least half the group's
# records carry becomes that group's value language
METRIC_PREFERENCE = ("projection", "adp", "rank", "position_rank")
# Lower is better for pick/ordinal metrics, so they enter negated
METRIC_SIGNS = {"projection": 1.0, "adp": -1.0, "rank": -1.0, "position_rank": -1.0}


def _group_metric(records) -> Optional[str]:
    for metric in METRIC_PREFERENCE:
        have = sum(1 for record in records if getattr(record, metric) is not None)
        if have * 2 >= len(records) and have > 0:
            return metric
    return None


def _zscores(records, metric) -> Dict[str, float]:
    valued = [
        (record.canonical_name, METRIC_SIGNS[metric] * getattr(record, metric))
        for record in records
        if getattr(record, metric) is not None
    ]
    values = [value for _, value in valued]
    center = fmean(values)
    spread = pstdev(values)
    if spread == 0:
        return {name: 0.0 for name, _ in valued}
    return {name: (value - center) / spread for name, value in valued}


class _PlayerAccumulator:
    def __init__(self, position: str):
        self.position = position
        self.nfl_team: Optional[str] = None
        self.zscores: Dict[str, float] = {}  # source -> z
        self.projections: List[float] = []
        self.adps: List[float] = []
        self.ranks: List[float] = []
        self.tiers: List[int] = []


def blend_batches(
    batches: List[SourceRankingBatch],
    season: int,
    scoring_format: str,
    weights: Optional[Dict[str, float]] = None,
) -> BlendedRanking:
    """Combine the given batches into one BlendedRanking (not yet saved)"""
    weights = weights or {}
    players: Dict[str, _PlayerAccumulator] = {}
    sources_used = []

    for batch in batches:
        if not batch.success or not batch.records:
            continue
        # Group this source's resolved records by normalized position,
        # keeping one row per player (first wins; sources are pre-sorted)
        by_position: Dict[str, list] = {}
        seen = set()
        for record in batch.records:
            position = normalize_position(record.position)
            if record.canonical_name is None or position not in BLENDABLE_POSITIONS:
                continue
            if record.canonical_name in seen:
                continue
            seen.add(record.canonical_name)
            by_position.setdefault(position, []).append(record)

        contributed = False
        for position, records in by_position.items():
            metric = _group_metric(records)
            if metric is None:
                continue
            zscores = _zscores(records, metric)
            contributed = contributed or bool(zscores)
            for record in records:
                accumulator = players.setdefault(
                    record.canonical_name, _PlayerAccumulator(position)
                )
                if record.canonical_name in zscores:
                    accumulator.zscores[batch.source] = zscores[
                        record.canonical_name
                    ]
                accumulator.nfl_team = accumulator.nfl_team or record.nfl_team
                if record.projection is not None:
                    accumulator.projections.append(record.projection)
                if record.adp is not None:
                    accumulator.adps.append(record.adp)
                if record.rank is not None:
                    accumulator.ranks.append(record.rank)
                if record.tier is not None:
                    accumulator.tiers.append(record.tier)
        if contributed:
            sources_used.append(batch.source)

    records = []
    for canonical_name, accumulator in players.items():
        if not accumulator.zscores:
            continue
        total_weight = sum(
            weights.get(source, 1.0) for source in accumulator.zscores
        )
        if total_weight <= 0:
            continue
        blended_value = (
            sum(
                weights.get(source, 1.0) * zscore
                for source, zscore in accumulator.zscores.items()
            )
            / total_weight
        )
        records.append(
            BlendedRankingRecord(
                canonical_name=canonical_name,
                position=accumulator.position,
                nfl_team=accumulator.nfl_team,
                blended_value=round(blended_value, 4),
                blended_projection=(
                    round(fmean(accumulator.projections), 2)
                    if accumulator.projections
                    else None
                ),
                consensus_rank=(
                    round(fmean(accumulator.ranks), 2) if accumulator.ranks else None
                ),
                adp=round(fmean(accumulator.adps), 2) if accumulator.adps else None,
                tier=min(accumulator.tiers) if accumulator.tiers else None,
                source_values={
                    source: round(zscore, 4)
                    for source, zscore in accumulator.zscores.items()
                },
            )
        )
    records.sort(key=lambda record: record.blended_value, reverse=True)

    return BlendedRanking(
        season=season,
        scoring_format=scoring_format,
        source_weights={
            source: weights.get(source, 1.0) for source in sources_used
        },
        sources_used=sources_used,
        records=records,
    )
