# -*- coding: utf-8 -*-
"""
OWNER TENDENCY MATH FOR THE SIMULATION ENGINE (Phase 4)

Pure functions shared by the Monte Carlo engine, the profile builder,
and the backtester. Two integration stages, per the architecture review:

Stage 1 (position choice): blend the owner's round-bucket position
frequencies with the league's logistic-regression probabilities as
  w * owner_frequency + (1 - w) * model_probability
where w scales with the metric's sample size and floors to 0 — an
unknown or thin owner IS the generic model ("augments, not replaces").

Stage 2 (player choice): replace deterministic best-projection with
weighted sampling over the top-K candidates at the chosen position,
ranked by projection order and ADP together, with the sampling
temperature widened by the owner's reach SD (a chalky owner collapses
to today's behavior; a reacher spreads the distribution).

Everything here operates on plain dicts precomputed onto the league
before the hot loop starts — no Mongo, no pandas, no model objects.
"""
import math
from typing import Dict, List, Optional

# (first_round, last_round, label); None = open-ended
ROUND_BUCKETS = [(1, 2, "1-2"), (3, 5, "3-5"), (6, 9, "6-9"), (10, None, "10+")]

# Sample-size gating: below MIN_SAMPLE a metric contributes nothing;
# influence ramps linearly to MAX_PROFILE_WEIGHT at FULL_SAMPLE
MIN_SAMPLE = 5
FULL_SAMPLE = 25
MAX_PROFILE_WEIGHT = 0.7

# Stage 2 sampling
TOP_K_CANDIDATES = 8
REACH_SD_REFERENCE = 6.0  # a "typical" owner's reach SD, in picks
TEMPERATURE_MIN = 0.4
TEMPERATURE_MAX = 2.5

# Inferred-miss detection (shared with profiling so the simulated
# behavior matches how the tendency was measured)
MISS_LOOKBACK = 3
MISS_ADP_BEFORE = 2
MISS_ADP_AFTER = 6


def bucket_for_round(round_num: int) -> str:
    for first, last, label in ROUND_BUCKETS:
        if round_num >= first and (last is None or round_num <= last):
            return label
    return ROUND_BUCKETS[-1][2]


def profile_weight(n: int) -> float:
    """How much an owner metric with n observations may move the engine"""
    if n < MIN_SAMPLE:
        return 0.0
    return MAX_PROFILE_WEIGHT * min(1.0, n / FULL_SAMPLE)


def blend_position_weights(
    model_weights: Dict[str, float],
    tendencies: dict,
    round_num: int,
    missed_position: Optional[str] = None,
) -> Dict[str, float]:
    """
    Stage 1: owner-frequency blend plus the inferred post-miss shift,
    renormalized. Positions the model never saw stay out of play.
    """
    blended = dict(model_weights)

    bucket = (
        tendencies.get("position_frequency", {}).get(bucket_for_round(round_num))
        or {}
    )
    weight = profile_weight(bucket.get("n", 0))
    if weight > 0:
        shares = bucket.get("shares", {})
        blended = {
            position: weight * shares.get(position, 0.0)
            + (1 - weight) * probability
            for position, probability in blended.items()
        }

    post_miss = tendencies.get("post_miss", {})
    if (
        missed_position
        and missed_position in blended
        and post_miss.get("n", 0) >= MIN_SAMPLE
    ):
        shift = post_miss.get("shift", 0.0) * profile_weight(post_miss["n"])
        blended[missed_position] = max(0.0, blended[missed_position] + shift)

    total = sum(blended.values())
    if total <= 0:
        return dict(model_weights)
    return {position: value / total for position, value in blended.items()}


def reach_sd_for(team_tendencies: dict, generic_tendencies: dict) -> float:
    """The owner's reach SD if well-sampled, else the league-generic one"""
    reach = (team_tendencies or {}).get("reach", {})
    if reach.get("n", 0) >= MIN_SAMPLE and reach.get("sd_delta") is not None:
        return reach["sd_delta"]
    generic = (generic_tendencies or {}).get("reach_sd")
    return generic if generic is not None else REACH_SD_REFERENCE


def candidate_weights(
    adps: List[Optional[float]], pick_number: int, reach_sd: float
) -> List[float]:
    """
    Stage 2 sampling weights for projection-ordered candidates. Each
    candidate's cost is the average of its projection rank and its ADP
    rank among the candidates (no ADP = neutral); weights decay
    exponentially with cost at a temperature set by the owner's reach SD.
    """
    count = len(adps)
    adp_rank = list(range(count))  # neutral default: projection order
    with_adp = sorted(
        (index for index in range(count) if adps[index] is not None),
        key=lambda index: adps[index],
    )
    for rank, index in enumerate(with_adp):
        adp_rank[index] = rank

    temperature = min(
        TEMPERATURE_MAX,
        max(TEMPERATURE_MIN, (reach_sd or REACH_SD_REFERENCE) / REACH_SD_REFERENCE),
    )
    return [
        math.exp(-((index + adp_rank[index]) / 2) / temperature)
        for index in range(count)
    ]


def build_team_tendencies(metrics: dict, profile_key: str) -> dict:
    """The subset of an OwnerProfile the engine needs, as a plain dict"""
    return {
        "profile_key": profile_key,
        "position_frequency": metrics.get("position_frequency", {}),
        "reach": metrics.get("reach", {}),
        "post_miss": metrics.get("post_miss", {}),
    }


def build_generic_tendencies(metrics_list: List[dict]) -> dict:
    """
    League-generic reach behavior pooled (sample-size weighted) across
    all known owners — the fallback that gives even unprofiled teams
    realistic player-level variance
    """
    weighted_sd = 0.0
    weighted_mean = 0.0
    total_n = 0
    for metrics in metrics_list:
        reach = metrics.get("reach", {})
        n = reach.get("n", 0)
        if n <= 0 or reach.get("sd_delta") is None:
            continue
        weighted_sd += reach["sd_delta"] * n
        weighted_mean += reach.get("mean_delta", 0.0) * n
        total_n += n
    if total_n == 0:
        return {}
    return {
        "reach_sd": round(weighted_sd / total_n, 2),
        "reach_mean": round(weighted_mean / total_n, 2),
        "n": total_n,
    }
