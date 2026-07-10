# -*- coding: utf-8 -*-
"""
OWNER TENDENCY PROFILING (frequency/average based, no ML)

Turns historical_picks into per-owner OwnerProfile documents. Everything
is counts and (recency-weighted) averages, and every metric carries its
raw sample size n so Phase 4's simulation integration can apply
frequency floors and fall back to the generic league model when an owner
is thin on data.

Ground rules from the architecture review:
- auction seasons are excluded entirely (bids aren't pick tendencies)
- keeper picks are excluded as owner choices but kept as board context
- "behavior after missing a target" is an inferred proxy (draft logs
  don't record intent) and is labeled as such
- owners merge across accounts/leagues via owner_aliases; the default
  identity is the stable ESPN member GUID
"""
from datetime import datetime
import math
from typing import Dict, List, Optional

from models.sources import HistoricalPick, OwnerAlias, OwnerProfile

# Weight = RECENCY_DECAY ** (current_season - season): a 2015 tendency
# says less about this year than a 2025 one does
RECENCY_DECAY = 0.9

# (first_round, last_round, label); None = open-ended
ROUND_BUCKETS = [(1, 2, "1-2"), (3, 5, "3-5"), (6, 9, "6-9"), (10, None, "10+")]

REACH_THRESHOLD_PICKS = 6  # taken >= half a round (12-team) early = a reach
RUN_LOOKBACK = 5  # a positional run = >= RUN_MIN of one position...
RUN_MIN = 3  # ...within the previous RUN_LOOKBACK picks
MISS_LOOKBACK = 3  # a target "sniped" this many picks before their turn
# A plausible target's ADP sits near the owner's slot: not long gone
# (adp >= slot - MISS_ADP_BEFORE) and not a later-round pick anyway
MISS_ADP_BEFORE = 2
MISS_ADP_AFTER = 6

ONESIE_POSITIONS = ["qb", "te", "dst", "k"]

PROFILE_POSITIONS = ["qb", "rb", "wr", "te", "dst", "k"]


def bucket_for_round(round_num: int) -> str:
    for first, last, label in ROUND_BUCKETS:
        if round_num >= first and (last is None or round_num <= last):
            return label
    return ROUND_BUCKETS[-1][2]


def _weighted_mean_sd(values: List[float], weights: List[float]):
    total = sum(weights)
    if not values or total <= 0:
        return None, None
    mean = sum(v * w for v, w in zip(values, weights)) / total
    variance = sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / total
    return mean, math.sqrt(variance)


class _OwnerAccumulator:
    def __init__(self, profile_key: str):
        self.profile_key = profile_key
        self.member_guids = set()
        self.display_names = set()
        self.league_ids = set()
        self.seasons = set()
        self.events = []  # non-keeper picks with context, one dict each

    def add_event(self, **event):
        self.events.append(event)


def _season_context(picks: List[HistoricalPick]) -> List[dict]:
    """
    Walk one league-season's board in pick order and emit, per non-keeper
    pick, the owner event with its board context (runs, sniped targets)
    """
    board = sorted(picks, key=lambda pick: pick.overall_pick)
    events = []
    for index, pick in enumerate(board):
        if pick.is_keeper or not pick.member_guid:
            continue  # keepers/unowned slots are context, not choices
        recent = board[max(0, index - RUN_LOOKBACK): index]
        run_position = None
        positions = [p.position for p in recent if p.position]
        for position in set(positions):
            if positions.count(position) >= RUN_MIN:
                run_position = position
                break

        # Inferred miss: a plausible target (ADP near this owner's slot)
        # went off the board within the last MISS_LOOKBACK picks
        missed_position = None
        best_distance = None
        for prior in board[max(0, index - MISS_LOOKBACK): index]:
            if prior.historical_adp is None or not prior.position:
                continue
            adp = prior.historical_adp
            if (
                pick.overall_pick - MISS_ADP_BEFORE
                <= adp
                <= pick.overall_pick + MISS_ADP_AFTER
            ):
                distance = abs(adp - pick.overall_pick)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    missed_position = prior.position

        events.append(
            {
                "member_guid": pick.member_guid,
                "display_name": pick.owner_display_name,
                "league_id": pick.espn_league_id,
                "season": pick.season,
                "round_num": pick.round_num,
                "bucket": bucket_for_round(pick.round_num),
                "position": pick.position,
                "overall_pick": pick.overall_pick,
                "adp_delta": (
                    pick.overall_pick - pick.historical_adp
                    if pick.historical_adp is not None
                    else None
                ),
                "run_position": run_position,
                "missed_position": missed_position,
            }
        )
    return events


def _position_frequency(events, weight_of) -> dict:
    frequency = {}
    for _, _, label in ROUND_BUCKETS:
        bucket_events = [
            e for e in events if e["bucket"] == label and e["position"]
        ]
        if not bucket_events:
            frequency[label] = {"n": 0, "shares": {}}
            continue
        totals: Dict[str, float] = {}
        for event in bucket_events:
            totals[event["position"]] = (
                totals.get(event["position"], 0.0) + weight_of(event)
            )
        grand_total = sum(totals.values())
        frequency[label] = {
            "n": len(bucket_events),
            "shares": {
                position: round(total / grand_total, 4)
                for position, total in sorted(totals.items())
            },
        }
    return frequency


def _reach_stats(events, weight_of) -> dict:
    valued = [e for e in events if e["adp_delta"] is not None]
    if not valued:
        return {"n": 0}
    deltas = [e["adp_delta"] for e in valued]
    weights = [weight_of(e) for e in valued]
    mean, sd = _weighted_mean_sd(deltas, weights)
    reach_weight = sum(
        w for e, w in zip(valued, weights)
        if e["adp_delta"] <= -REACH_THRESHOLD_PICKS
    )
    return {
        "n": len(valued),
        "mean_delta": round(mean, 2),
        "sd_delta": round(sd, 2),
        "reach_rate": round(reach_weight / sum(weights), 4),
        "threshold_picks": REACH_THRESHOLD_PICKS,
    }


def _run_participation(events, weight_of) -> dict:
    opportunities = [e for e in events if e["run_position"] and e["position"]]
    if not opportunities:
        return {"n": 0}
    total = sum(weight_of(e) for e in opportunities)
    joined = sum(
        weight_of(e)
        for e in opportunities
        if e["position"] == e["run_position"]
    )
    return {"n": len(opportunities), "rate": round(joined / total, 4)}


def _post_miss(events, frequency, weight_of) -> dict:
    """
    Inferred (proxy) metric: when a plausible target at position p was
    taken just before this owner's turn, do they still take p (chase) or
    pivot? Compared against their own baseline share of p in that bucket.
    """
    misses = [e for e in events if e["missed_position"] and e["position"]]
    if not misses:
        return {"n": 0, "inferred": True}
    total = sum(weight_of(e) for e in misses)
    chased = sum(
        weight_of(e) for e in misses if e["position"] == e["missed_position"]
    )
    baselines = []
    for event in misses:
        shares = frequency.get(event["bucket"], {}).get("shares", {})
        baselines.append(shares.get(event["missed_position"], 0.0))
    chase_rate = chased / total
    baseline = sum(baselines) / len(baselines)
    return {
        "n": len(misses),
        "chase_rate": round(chase_rate, 4),
        "baseline_share": round(baseline, 4),
        "shift": round(chase_rate - baseline, 4),
        "inferred": True,  # draft logs record picks, not intent
    }


def _onesie_timing(events, weight_of) -> dict:
    timing = {}
    for position in ONESIE_POSITIONS:
        firsts = {}  # (league, season) -> earliest round taken
        for event in events:
            if event["position"] != position:
                continue
            key = (event["league_id"], event["season"])
            firsts[key] = min(
                firsts.get(key, event["round_num"]), event["round_num"]
            )
        if not firsts:
            timing[position] = {"n": 0}
            continue
        rounds = list(firsts.values())
        weights = [
            weight_of({"season": season}) for (_, season) in firsts.keys()
        ]
        mean, _ = _weighted_mean_sd([float(r) for r in rounds], weights)
        timing[position] = {
            "n": len(rounds),
            "mean_first_round": round(mean, 2),
            "earliest": min(rounds),
            "latest": max(rounds),
        }
    return timing


def extract_profiles(
    picks: List[HistoricalPick],
    alias_map: Optional[Dict[str, str]] = None,
    current_season: Optional[int] = None,
) -> List[OwnerProfile]:
    """Pure function: historical picks -> OwnerProfile documents"""
    alias_map = alias_map or {}
    if current_season is None:
        current_season = max((pick.season for pick in picks), default=0)

    def weight_of(event) -> float:
        return RECENCY_DECAY ** (current_season - event["season"])

    # Group by league-season; auction seasons are excluded entirely
    seasons: Dict[tuple, List[HistoricalPick]] = {}
    for pick in picks:
        seasons.setdefault((pick.espn_league_id, pick.season), []).append(pick)

    owners: Dict[str, _OwnerAccumulator] = {}
    for (league_id, _season), season_picks in sorted(seasons.items()):
        if any(pick.bid_amount for pick in season_picks):
            continue  # auction year: bids aren't snake-draft tendencies
        for event in _season_context(season_picks):
            profile_key = alias_map.get(
                event["member_guid"], event["member_guid"]
            )
            owner = owners.setdefault(profile_key, _OwnerAccumulator(profile_key))
            owner.member_guids.add(event["member_guid"])
            if event["display_name"]:
                owner.display_names.add(event["display_name"])
            owner.league_ids.add(event["league_id"])
            owner.seasons.add(event["season"])
            owner.add_event(**event)

    profiles = []
    for owner in owners.values():
        frequency = _position_frequency(owner.events, weight_of)
        profiles.append(
            OwnerProfile(
                profile_key=owner.profile_key,
                display_names=sorted(owner.display_names),
                member_guids=sorted(owner.member_guids),
                espn_league_ids=sorted(owner.league_ids),
                seasons_observed=sorted(owner.seasons),
                total_picks_observed=len(owner.events),
                metrics={
                    "position_frequency": frequency,
                    "reach": _reach_stats(owner.events, weight_of),
                    "run_participation": _run_participation(
                        owner.events, weight_of
                    ),
                    "post_miss": _post_miss(owner.events, frequency, weight_of),
                    "onesie_timing": _onesie_timing(owner.events, weight_of),
                    "recency_decay": RECENCY_DECAY,
                    "reference_season": current_season,
                },
                generated_at=datetime.now(),
            )
        )
    return profiles


async def load_alias_map(engine) -> Dict[str, str]:
    aliases = await engine.find(OwnerAlias)
    return {alias.member_guid: alias.profile_key for alias in aliases}


async def build_owner_profiles(
    engine, current_season: Optional[int] = None
) -> dict:
    """Full rebuild: replace every stored profile from historical_picks"""
    picks = await engine.find(HistoricalPick)
    alias_map = await load_alias_map(engine)
    profiles = extract_profiles(
        list(picks), alias_map=alias_map, current_season=current_season
    )
    await engine.get_collection(OwnerProfile).delete_many({})
    if profiles:
        await engine.save_all(profiles)
    return {
        "profiles": len(profiles),
        "owners": [profile.profile_key for profile in profiles],
        "picks_considered": len(picks),
    }
