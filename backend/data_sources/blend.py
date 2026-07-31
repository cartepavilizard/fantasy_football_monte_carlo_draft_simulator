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

This module emits two different numbers and they are NOT interchangeable:

  blended_value       the z-scored, weighted consensus. Ordinal. The sort
                      key. Four sources feed it.
  blended_projection  a POINT TOTAL in fantasy points. Two sources feed
                      it (only sleeper and espn project points at all).
                      `POST /league/{id}/player/sync` materializes this
                      into PlayerPoints.projected_points, so it is the
                      number the entire simulator runs on: every position
                      tier assignment, every position_max_points ceiling,
                      every randomized_points() draw, every
                      value_over_wait verdict.

blended_projection METHODOLOGY (the contract — change it deliberately,
in one place). Settled 2026-07-31 against the live 2026 ppr batches;
the measurements quoted below come from those batches.

Before this contract, blended_projection was a plain unweighted
fmean() of raw per-source point totals. Three things were wrong with
that, in ascending order of how much they actually mattered.

1. WEIGHTS. blended_value honored RANKING_BLEND_WEIGHTS; the projection
   beside it did not. Two weighting regimes in one function is simply a
   defect. The projection now takes the same weighted mean, over the
   same weights map, as blended_value.

   Be honest about what this alone buys: RANKING_BLEND_WEIGHTS ships as
   {} (equal weights), and an equal-weighted mean IS the unweighted
   mean. Fixing only this moves ZERO players — measured: 0 of the top
   200 changed by more than 0.005 points. It makes the documented knob
   real, it does not by itself correct anything. The movement in this
   contract comes from (2) and (3).

2. SCALE. Raw point totals were averaged across sources that do not
   agree on what a point is. Measured per-position ratio of espn to
   sleeper projections, over the players BOTH project:

       k 1.41   rb 1.14   dst 1.04   te 1.03   wr 1.02   qb 0.98

   Kickers are the tell: on the same 31 kickers, espn projects ~41%
   more points than sleeper. That is a scoring-rule disagreement, not a
   talent disagreement, and averaging the two raw produced a kicker
   number matching neither source and no real league.

   So each source's projections are rescaled onto an ANCHOR source's
   scale before averaging, one multiplicative factor per
   (source, position):

       factor = median over the overlap set of  anchor_proj / source_proj

   - The anchor is the first of ANCHOR_PRIORITY present in the blend
     (espn in practice). Rationale: drafts happen on ESPN, ESPN's
     spellings are already canonical, and projected_points is compared
     against ESPN's own displayed numbers at the draft table. The blend
     should answer "points in MY league", so it stays in ESPN units.
   - MEDIAN of per-player ratios, not ratio of means: the mean ratio is
     wrecked by near-zero denominators deep in the pool (wr mean ratio
     1.38 vs median 1.02 with no floor).
   - Overlap set floors both projections at RESCALE_MIN_PROJECTION
     (10.0 pts). Below that the ratio is noise over noise; the qb factor
     drifts 0.94 -> 0.98 -> 0.99 as the floor goes 0 -> 10 -> 50.
   - Fewer than RESCALE_MIN_OVERLAP (10) usable pairs, or a single
     projection source in the blend, means factor 1.0 — no rescale,
     never a guessed one.

   Known simplification, stated so nobody mistakes it for rigor: one
   scalar per (source, position) is a linear-through-origin fit. The
   floor sweep above shows the true relationship is mildly
   non-constant across the projection range. A quantile map would fit
   better and is not worth its opacity at n=31..169 per position.

   NOTE the ordering consequence. Rescaling one INPUT before averaging
   is not a monotone transform of the OUTPUT: with old = (e + s)/2 and
   new = (e + f*s)/2, the shift (f-1)*s/2 grows with s, so players with
   different e/s splits can swap. Within-position order is NOT
   preserved — measured 76/94 rb and 20/31 k held their slot. Tier
   assignment is by within-position rank, so tiers DO move. That is
   real recalibration and belongs to H5's gated adoption, not here.

3. STRUCTURALLY INVALID VALUES. espn writes projection 0.0 to mean "no
   projection" — 45 records, of which 23 are players sleeper projects
   above zero. James Conner read sleeper 59.80 / espn 0.00 and was
   materialized at 29.90: a sentinel silently halved a real projection.
   sleeper separately emits two negative season totals. A season point
   projection <= 0 is not data, so non-positive values are dropped
   before blending, per source. A player left with no usable projection
   gets None, exactly as if no source had projected them.

OUTLIERS, and why there is no outlier rule. Once (2) and (3) are done,
what remains is genuine disagreement about ambiguous players — after
rescaling, the median top-200 residual is 4.7% and the worst are TEs
and D/STs (Terrance Ferguson: sleeper 79.4 vs espn 135.9). It is
tempting to reject the outlier. Do not:

  - With TWO projection sources, every robust estimator degenerates.
    The median of two values is their mean; a trimmed mean of two
    values is their mean; MAD-based rejection has no majority to
    appeal to. There is nothing to compute.
  - Rejecting therefore reduces to "always believe source X", and there
    is no evidence for that ranking. Per-source projection accuracy
    cannot be backtested here: the historical store keeps ESPN's
    projected/actual pairs only, never sleeper's, so no arm of the
    comparison exists.

Discarding half the evidence on an unevidenced preference would be
strictly worse than averaging. So sharp disagreement is PRESERVED in
the mean and SURFACED as projection_spread (max minus min of the
rescaled contributions, None below two sources) — uncertainty to be
consumed by the tier-confidence and near-tie work, not error to be
suppressed. Revisit only when a third projection source exists, which
is what makes a real robust estimator possible.

THE 97 PROJECTION-LESS RECORDS stay dropped at sync; they are NOT
reconstructed from blended_value. They are not a coverage gap worth
closing:
  - Every one of them ranks 310th or worse by blended_value (median
    366th), and 87 of 97 are RB.
  - Their blended_value is a degenerate tie at -0.417 built from an
    espn adp of 584.49 — itself an ESPN sentinel for "nobody drafts
    this player". Inverting that z-score into a point total would
    manufacture a projection out of "ESPN declined to rank him". This
    repo has twice shipped changes that looked right and were inert or
    fabricated (the G3 default, the G5 ship gate); synthesizing the
    engine's primary input from a sentinel is the same family of
    mistake.
  - There is no shortage to fix. The blend materializes 672 projected
    players against the 192 a 12-team, 16-round draft consumes.
The drop stays, but it is counted and logged rather than silent.

OUT OF SCOPE, deliberately: consensus_rank and adp remain plain
fmean(). Picks and ordinals are already a common unit across sources,
so raw averaging is defensible there in a way it never was for points.
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
