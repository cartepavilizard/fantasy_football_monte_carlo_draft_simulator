# -*- coding: utf-8 -*-
"""
PLAYOFF STRENGTH OF SCHEDULE (PHASE C, TASK C5)

Weeks 14-16 (the fantasy-playoff window, PLAYOFF_SOS_WEEKS) strength of
schedule per NFL team, per position: sum C2's defense_position_strength()
multipliers across that team's opponents in the window. Same rank
convention as C2 (rank 1 = highest sum = easiest/softest schedule, best
for the offense player) and the same neutral-early-season story — this
module adds no new signal, it only re-slices C2's opponent-vs-position
table into a playoff-specific window. Mongo-only by construction
(opponent_map() and defense_position_strength() both read straight from
Mongo), so it inherits inseason_api.py's cached-only constraint without
importing data_sources.

Byes count as zero, not a skip: a team on bye during the window gets no
entry for that week rather than being reweighted around it — a playoff
bye is a real zero-point week for anyone rostering that team's players,
and the sum should show it as worse, not average it away. games_scheduled
and bye_weeks are reported alongside so a low score is legible as "fewer
games", not silently folded into "soft schedule".

Confidence: each opponent-week's confidence comes straight from C2; the
team-position score reports the WEAKEST (least confident) of its
opponent-weeks, since a sum is only as trustworthy as its least-sampled
term. The top-level `note` is C2's own early-season "all neutral" note
(defense_position_strength() returns it once nothing has been sampled
yet), carried through unchanged so early-season callers say so plainly
instead of presenting a confident-looking ranking of noise.

Roster join (optional, playoff_sos_for_league): scoped to one league,
each fantasy team's CURRENT starters (latest_scoring_period, non-bench/IR
slots) are looked up by (nfl_team, position) against the same table —
"how friendly are your starters' playoff schedules". average_rank is the
mean of each starter's position rank (lower = easier); starters whose NFL
team has no schedule data yet (bye all window, or team unknown) carry a
null playoff_sos and are excluded from that average, not zeroed into it.
"""
from typing import Dict, List, Optional

from .config import PLAYOFF_SOS_WEEKS
from .inseason import InSeasonLeague, TeamWeekRoster
from .matchup_strength import (
    STRENGTH_POSITIONS,
    defense_position_strength,
    opponent_map,
    strength_for,
)

# Slots that are never part of the playoff-relevant starting lineup
BENCH_SLOTS = ("BE", "IR")

CONFIDENCE_ORDER = ["none", "low", "medium", "high"]


def _weakest_confidence(confidences: List[str]) -> str:
    if not confidences:
        return "none"
    return min(confidences, key=CONFIDENCE_ORDER.index)


async def playoff_schedule_strength(
    engine,
    season: int,
    weeks: Optional[List[int]] = None,
    league_ids: Optional[List[int]] = None,
) -> dict:
    """
    position -> nfl_team -> {score, games_scheduled, bye_weeks, opponents,
    confidence, rank}. `weeks` defaults to PLAYOFF_SOS_WEEKS; `league_ids`
    passes through to defense_position_strength() the same way C2's own
    endpoint does (unused today, kept for the same optional narrowing).
    """
    weeks = list(weeks) if weeks else list(PLAYOFF_SOS_WEEKS)
    opponents = await opponent_map(engine, season)
    strength = await defense_position_strength(
        engine, season, league_ids=league_ids
    )

    nfl_teams = sorted({team for team, _week in opponents.keys()})

    positions: Dict[str, dict] = {}
    for position in STRENGTH_POSITIONS:
        by_team = {}
        for team in nfl_teams:
            window = []
            byes = []
            for week in weeks:
                opponent = opponents.get((team, week))
                if opponent is None:
                    byes.append(week)
                    continue
                matchup = strength_for(strength, position, opponent)
                window.append(
                    {
                        "week": week,
                        "opponent": opponent,
                        "multiplier": matchup["multiplier"],
                        "confidence": matchup["confidence"],
                    }
                )
            by_team[team] = {
                "score": round(sum(entry["multiplier"] for entry in window), 4),
                "games_scheduled": len(window),
                "bye_weeks": byes,
                "opponents": window,
                "confidence": _weakest_confidence(
                    [entry["confidence"] for entry in window]
                ),
            }
        # rank 1 = highest sum = easiest playoff schedule
        ordered = sorted(
            by_team.items(), key=lambda item: item[1]["score"], reverse=True
        )
        for rank, (_team, entry) in enumerate(ordered, start=1):
            entry["rank"] = rank
        positions[position] = by_team

    return {
        "season": season,
        "weeks": weeks,
        "positions": positions,
        "note": strength["note"],
    }


async def playoff_sos_for_league(
    engine, league: InSeasonLeague, season: int, sos: dict
) -> List[dict]:
    """
    Per-fantasy-team view of `sos` (a playoff_schedule_strength() result):
    each team's current starters joined against the table by (nfl_team,
    position), sorted friendliest-average-rank first. Reads the league's
    latest synced roster snapshot — no new data, just a re-slice.
    """
    week = league.latest_scoring_period
    rosters = await engine.find(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == league.espn_league_id)
        & (TeamWeekRoster.season == season)
        & (TeamWeekRoster.week == week),
    )
    team_names = {team.espn_team_id: team.name for team in league.teams}

    teams = []
    for roster in rosters:
        starters = []
        ranks = []
        for entry in roster.entries:
            if entry.lineup_slot in BENCH_SLOTS:
                continue
            position = (entry.position or "").upper()
            team_entry = (
                sos["positions"].get(position, {}).get(entry.nfl_team)
                if entry.nfl_team
                else None
            )
            starters.append(
                {
                    "player_name": entry.player_name,
                    "position": position,
                    "nfl_team": entry.nfl_team,
                    "lineup_slot": entry.lineup_slot,
                    "playoff_sos": team_entry,
                }
            )
            if team_entry is not None:
                ranks.append(team_entry["rank"])
        teams.append(
            {
                "espn_team_id": roster.espn_team_id,
                "team_name": team_names.get(roster.espn_team_id),
                "starters": starters,
                "average_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
            }
        )

    teams.sort(
        key=lambda team: (
            team["average_rank"] if team["average_rank"] is not None else float("inf")
        )
    )
    return teams
