# -*- coding: utf-8 -*-
"""
TRADE-WILLINGNESS OWNER PROFILES (PHASE E, TASK E3)

Extends the draft-tendency profiling philosophy (profiling.py) to
in-season `LeagueTransaction` data: frequencies and averages only, every
metric carries its raw sample size `n`, recency-weighted where seasons
mix (`RECENCY_DECAY`, imported not redeclared), and response-behavior
proxies are labeled `"inferred": true`. Full spec:
docs/specs/E3-trade-willingness-features.md.

COMPUTED ON READ, NOT STORED: one pass over one league's transactions is
cheap enough that there is nothing to precompute or invalidate. No new
collection; `OwnerProfile.metrics` (rebuilt wholesale from draft picks)
is never touched here.

Owner identity: a team's profile key is its `LeagueTeamInfo.owner_guid`
resolved through the existing `owner_aliases` mechanism
(`profiling.load_alias_map`) so the same owner across leagues merges;
teams with no `owner_guid` key on `f"team:{espn_league_id}:{team_id}"`
and never merge (by construction, since that key embeds the league id).

Position resolution caveat: `TransactionItem` carries no position field
(ESPN's transaction payload isn't re-fetched here), so `position_mix`
resolves each traded player's position best-effort from this league's
already-synced `TeamWeekRoster` entries and latest `FreeAgentSnapshot`
— zero new fetches, just reading collections B1 already populates.
Unresolved players bucket under `"UNK"` rather than being dropped, so
`n_players_sent` always accounts for every sent item.

The `unknown`-until-deadline rule (spec §3): a team with zero trades is
`"unknown"` while the league's trade deadline (or `DEADLINE_FALLBACK_WEEK`
when the league has none) hasn't passed yet, and only becomes credible
`"reluctant"` evidence afterward — the September-credibility rule from
C2/C4 applied to social data.
"""
import datetime
from typing import Dict, List, Optional

from odmantic import query

from profiling import RECENCY_DECAY, load_alias_map

from .inseason import (
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueTransaction,
    TeamWeekRoster,
)

# Thresholds are definitional (spec §3 rationale), not tunable knobs.
ACTIVE_TRADES_PER_SEASON = 2.0
ACTIVE_RELATIVE_RATE = 1.5

# A league without a trade_deadline falls back to this week (spec §6)
# for the unknown-until-deadline rule.
DEADLINE_FALLBACK_WEEK = 11

TIMING_BUCKETS = [
    ("early(1-5)", 1, 5),
    ("mid(6-9)", 6, 9),
    ("deadline(10+)", 10, None),
]

_WILLINGNESS_RANK = {"active": 0, "open": 1, "unknown": 2, "reluctant": 3}


class _TradeEvent:
    def __init__(self, txn: LeagueTransaction, team_id: int):
        self.season = txn.season
        self.week = txn.week
        self.initiated = txn.team_id == team_id
        self.sent_items = [item for item in txn.items if item.from_team_id == team_id]
        self.received_items = [
            item for item in txn.items if item.to_team_id == team_id
        ]
        self.partners = sorted(
            {
                other
                for item in txn.items
                for other in (item.from_team_id, item.to_team_id)
                if other is not None and other != team_id
            }
        )


class _ActivityEvent:
    def __init__(self, txn: LeagueTransaction):
        self.season = txn.season


def _week_bucket(week: Optional[int]) -> Optional[str]:
    if week is None:
        return None
    for label, low, high in TIMING_BUCKETS:
        if week >= low and (high is None or week <= high):
            return label
    return None


def _deal_shape(sent_count: int, received_count: int) -> str:
    pair = {sent_count, received_count}
    if pair == {1}:
        return "one_for_one"
    if pair == {1, 2}:
        return "two_for_one"
    return "bigger"


def _deal_shapes(trade_events: List[_TradeEvent], weight_of) -> dict:
    n = len(trade_events)
    if n == 0:
        return {"n": 0}
    shape_weight = {"one_for_one": 0.0, "two_for_one": 0.0, "bigger": 0.0}
    total_weight = 0.0
    sent_counts = []
    received_counts = []
    for event in trade_events:
        weight = weight_of(event)
        total_weight += weight
        shape_weight[_deal_shape(len(event.sent_items), len(event.received_items))] += weight
        sent_counts.append(len(event.sent_items))
        received_counts.append(len(event.received_items))
    return {
        "n": n,
        "one_for_one": round(shape_weight["one_for_one"] / total_weight, 4),
        "two_for_one": round(shape_weight["two_for_one"] / total_weight, 4),
        "bigger": round(shape_weight["bigger"] / total_weight, 4),
        "avg_players_sent": round(sum(sent_counts) / n, 4),
        "avg_players_received": round(sum(received_counts) / n, 4),
    }


def _position_mix(trade_events: List[_TradeEvent], weight_of, player_positions: dict) -> dict:
    sent_items = [item for event in trade_events for item in event.sent_items]
    if not sent_items:
        return {"n_players_sent": 0, "shares": {}}
    totals: Dict[str, float] = {}
    for event in trade_events:
        weight = weight_of(event)
        for item in event.sent_items:
            position = player_positions.get(item.player_id, "UNK")
            totals[position] = totals.get(position, 0.0) + weight
    grand_total = sum(totals.values())
    return {
        "n_players_sent": len(sent_items),
        "shares": {
            position: round(total / grand_total, 4)
            for position, total in sorted(totals.items())
        },
    }


def _timing(trade_events: List[_TradeEvent], weight_of) -> dict:
    n = len(trade_events)
    if n == 0:
        return {"n": 0, "buckets": {}}
    bucket_weight: Dict[str, float] = {}
    total_weight = 0.0
    for event in trade_events:
        bucket = _week_bucket(event.week)
        if bucket is None:
            continue
        weight = weight_of(event)
        bucket_weight[bucket] = bucket_weight.get(bucket, 0.0) + weight
        total_weight += weight
    if total_weight <= 0:
        return {"n": n, "buckets": {}}
    return {
        "n": n,
        "buckets": {
            bucket: round(weight / total_weight, 4)
            for bucket, weight in sorted(bucket_weight.items())
        },
    }


def _partners(trade_events: List[_TradeEvent], weight_of) -> dict:
    n = len(trade_events)
    if n == 0:
        return {"n_distinct": 0}
    partner_weight: Dict[int, float] = {}
    total_weight = 0.0
    for event in trade_events:
        weight = weight_of(event)
        total_weight += weight
        for partner in event.partners:
            partner_weight[partner] = partner_weight.get(partner, 0.0) + weight
    if not partner_weight:
        return {"n_distinct": 0}
    top = max(partner_weight.values())
    return {
        "n_distinct": len(partner_weight),
        "concentration": round(top / total_weight, 4) if total_weight > 0 else 0.0,
    }


def _initiations(trade_events: List[_TradeEvent], weight_of) -> dict:
    n = len(trade_events)
    if n == 0:
        return {"n": 0, "inferred": True}
    total_weight = sum(weight_of(event) for event in trade_events)
    initiated_weight = sum(
        weight_of(event) for event in trade_events if event.initiated
    )
    return {
        "n": n,
        "rate": round(initiated_weight / total_weight, 4) if total_weight > 0 else 0.0,
        "inferred": True,
    }


def _willingness_label(
    n_trades: int,
    trades_per_season: float,
    relative_trade_rate: Optional[float],
    deadline_passed: bool,
) -> str:
    if n_trades == 0:
        return "reluctant" if deadline_passed else "unknown"
    if trades_per_season >= ACTIVE_TRADES_PER_SEASON or (
        relative_trade_rate is not None and relative_trade_rate >= ACTIVE_RELATIVE_RATE
    ):
        return "active"
    return "open"


def willingness_features(
    transactions: List[LeagueTransaction],
    league: InSeasonLeague,
    alias_map: Dict[str, str],
    now: datetime.datetime,
    player_positions: Optional[Dict[int, str]] = None,
) -> Dict[str, dict]:
    """
    PURE: per-team trade_willingness feature dicts for one league (the
    testable core). Returns {team_id (str): {team_id, team_name,
    owner_name, profile_key, trade_willingness}}. transactions may span
    multiple seasons (recency-weighted) even though today's sync horizon
    is single-season — the season field is honored now so a future
    multi-season backfill needs no changes here.
    """
    player_positions = player_positions or {}

    def weight_of(event) -> float:
        return RECENCY_DECAY ** (league.season - event.season)

    trade_events: Dict[int, List[_TradeEvent]] = {team.espn_team_id: [] for team in league.teams}
    activity_events: Dict[int, List[_ActivityEvent]] = {
        team.espn_team_id: [] for team in league.teams
    }
    seasons_observed: Dict[int, set] = {
        team.espn_team_id: {league.season} for team in league.teams
    }
    n_vetoed_league = 0
    # League totals for the two means below are one weighted unit PER
    # DISTINCT TRANSACTION, not summed per-team appearances — a 2-team
    # trade must not count double just because both sides record it in
    # their own trade_events (spec §7: "5 executed trades / 10 teams"
    # means 5 transactions, giving a league mean of 0.5, not 1.0).
    total_trade_weight = 0.0
    total_move_weight = 0.0

    for txn in transactions:
        kind = (txn.type or "").upper()
        status = (txn.status or "").upper()
        weight = RECENCY_DECAY ** (league.season - txn.season)
        if "TRADE" in kind:
            if status == "VETOED":
                n_vetoed_league += 1
                continue
            if status != "EXECUTED":
                continue
            involved = sorted(
                {
                    other
                    for item in txn.items
                    for other in (item.from_team_id, item.to_team_id)
                    if other is not None
                }
            )
            total_trade_weight += weight
            for team_id in involved:
                if team_id not in trade_events:
                    continue  # team not on this league's current roster
                trade_events[team_id].append(_TradeEvent(txn, team_id))
                seasons_observed[team_id].add(txn.season)
        elif kind in ("WAIVER", "FREEAGENT") and status == "EXECUTED":
            total_move_weight += weight
            if txn.team_id in activity_events:
                activity_events[txn.team_id].append(_ActivityEvent(txn))
                seasons_observed[txn.team_id].add(txn.season)

    if league.trade_deadline is not None:
        deadline_passed = now >= league.trade_deadline
    else:
        deadline_passed = league.latest_scoring_period >= DEADLINE_FALLBACK_WEEK

    # Per-team n_trades/n_moves and their own per-season rates
    per_team_rate = {}
    for team in league.teams:
        team_id = team.espn_team_id
        n_seasons = len(seasons_observed[team_id])
        weighted_trades = sum(weight_of(event) for event in trade_events[team_id])
        weighted_moves = sum(weight_of(event) for event in activity_events[team_id])
        per_team_rate[team_id] = {
            "n_trades": len(trade_events[team_id]),
            "trades_per_season": round(weighted_trades / n_seasons, 4),
            "n_moves": len(activity_events[team_id]),
            "moves_per_season": round(weighted_moves / n_seasons, 4),
        }

    team_count = len(league.teams) or 1
    league_mean_trades_per_season = round(total_trade_weight / team_count, 4)
    league_mean_moves_per_season = round(total_move_weight / team_count, 4)

    owners: Dict[str, dict] = {}
    for team in league.teams:
        team_id = team.espn_team_id
        rate = per_team_rate[team_id]
        n_trades = rate["n_trades"]
        trades_per_season = rate["trades_per_season"]
        relative_trade_rate = (
            round(trades_per_season / league_mean_trades_per_season, 4)
            if league_mean_trades_per_season > 0
            else None
        )
        profile_key = (
            alias_map.get(team.owner_guid, team.owner_guid)
            if team.owner_guid
            else f"team:{league.espn_league_id}:{team_id}"
        )
        trade_willingness = {
            "n_trades": n_trades,
            "n_seasons_observed": len(seasons_observed[team_id]),
            "trades_per_season": trades_per_season,
            "league_mean_trades_per_season": league_mean_trades_per_season,
            "relative_trade_rate": relative_trade_rate,
            "activity": {
                "n_moves": rate["n_moves"],
                "moves_per_season": rate["moves_per_season"],
                "league_mean_moves_per_season": league_mean_moves_per_season,
            },
            "deal_shapes": _deal_shapes(trade_events[team_id], weight_of),
            "position_mix": _position_mix(
                trade_events[team_id], weight_of, player_positions
            ),
            "timing": _timing(trade_events[team_id], weight_of),
            "partners": _partners(trade_events[team_id], weight_of),
            "initiations": _initiations(trade_events[team_id], weight_of),
            "veto_context": {"n_vetoed_league": n_vetoed_league},
            "willingness": _willingness_label(
                n_trades, trades_per_season, relative_trade_rate, deadline_passed
            ),
        }
        owners[str(team_id)] = {
            "team_id": team_id,
            "team_name": team.name,
            "owner_name": team.owner_name,
            "profile_key": profile_key,
            "trade_willingness": trade_willingness,
        }
    return owners


def _sort_key(owner: dict) -> tuple:
    willingness = owner["trade_willingness"]
    return (
        _WILLINGNESS_RANK.get(willingness["willingness"], 99),
        -willingness["trades_per_season"],
    )


async def _player_positions(engine, espn_league_id: int, season: int) -> Dict[int, str]:
    """
    Best-effort player_id -> position lookup from already-synced roster
    and free-agent data (TransactionItem itself carries no position).
    """
    positions: Dict[int, str] = {}
    rosters = await engine.find(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == espn_league_id)
        & (TeamWeekRoster.season == season),
    )
    for roster in rosters:
        for entry in roster.entries:
            if entry.position:
                positions[entry.player_id] = entry.position.upper()
    snapshot = await engine.find_one(
        FreeAgentSnapshot,
        (FreeAgentSnapshot.espn_league_id == espn_league_id)
        & (FreeAgentSnapshot.season == season),
        sort=(query.desc(FreeAgentSnapshot.synced_at), query.desc(FreeAgentSnapshot.id)),
    )
    if snapshot:
        for entry in snapshot.entries:
            if entry.position:
                positions.setdefault(entry.player_id, entry.position.upper())
    return positions


async def league_trade_willingness(engine, espn_league_id: int, season: int) -> dict:
    """
    {week, owners: [...]} sorted most-willing first (active > open >
    unknown > reluctant, then trades_per_season) — the endpoint's data.
    """
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        return {"week": None, "owners": []}
    transactions = await engine.find(
        LeagueTransaction,
        (LeagueTransaction.espn_league_id == espn_league_id)
        & (LeagueTransaction.season == season),
    )
    alias_map = await load_alias_map(engine)
    player_positions = await _player_positions(engine, espn_league_id, season)
    owners = willingness_features(
        list(transactions),
        league,
        alias_map,
        datetime.datetime.now(),
        player_positions=player_positions,
    )
    ordered = sorted(owners.values(), key=_sort_key)
    return {"week": league.latest_scoring_period, "owners": ordered}
