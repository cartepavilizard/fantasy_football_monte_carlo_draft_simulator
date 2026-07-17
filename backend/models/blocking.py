# -*- coding: utf-8 -*-
"""
BLOCKING PLAYS (PHASE E, TASK E5 — rivals' injured-star handcuffs)

The denial half of "spend a bench spot to keep a player off the board".
Where E6 (hoarding) is the broad post-waivers scan over the FA pool, E5
owns one specific, high-leverage join: a RIVAL's injured starter whose
direct handcuff is sitting in the free-agent pool. Grabbing that handcuff
purely to deny the rival the insurance is a blocking play — it is worth a
roster spot even when it has near-zero value to YOU, because the rival
would otherwise start them.

THE BOUNDARY (E6 spec §1, normative here): injured-star-handcuff cases
belong to E5 and are EXCLUDED from E6's candidate pool. E6 must not
double-flag them. This module owns the single source of truth for that
exclusion — `rival_injured_star_handcuff_ids()` — which E6 imports and
subtracts from its candidate set, so the boundary lives in one place
and is tested from both sides (E5 claims it, E6 omits it).

The join is three tables over RIVALS' rosters (everyone except the
user's team, per ESPN_MY_TEAMS):

  C7's curated starter -> handcuff map (models/handcuffs.py)
    AND
  D2's injury designations (models/inseason.InjuryDesignation) OR the
  roster entry's ESPN injury_status — D2 wins when present (E1 §3.2's
  precedence), since the official game-status designation is newer than
  the synced roster tag
    AND
  the latest FreeAgentSnapshot (the handcuff must actually be claimable)

A starter is "injured enough" to make their handcuff a denial target
when the effective status is questionable/doubtful/out/IR/PUP — the same
urgent set C7 uses for high-priority insurance flags, plus IR/PUP
because a multi-week-out star's handcuff is the classic season-long
denial play (the starter isn't coming back to displace them soon).

ON-DEMAND, NOT STORED: unlike E6 (whose scan is expensive and lives in
a stored weekly report), this join is cheap — a handful of Mongo reads
and an in-memory match. So `blocking_plays()` computes on demand and the
GET endpoint calls it directly. It still inherits B4's cached-only
constraint: no data_sources import, no fetches, just Mongo reads.

DENIAL COPY (C8): speaks in whose workload the handcuff inherits and how
hurt the starter is — never last week's fantasy points. The same
insurance/opportunity register C7 uses, reframed as denial ("deny
<rival> the insurance") rather than self-insurance.
"""
import datetime
from typing import Dict, List, Optional, Set, Tuple

from odmantic import query

from .config import ESPN_MY_TEAMS
from .handcuffs import HANDCUFF_URGENT_INJURY_STATUSES, list_handcuffs
from .inseason import (
    FreeAgentSnapshot,
    InjuryDesignation,
    InSeasonLeague,
    TeamWeekRoster,
)

# A rival starter is "injured enough" that their handcuff is a denial
# target. C7's urgent set (questionable/doubtful/out) plus IR/PUP: a
# multi-week-out star's backup is the season-long blocking play (the
# starter isn't reclaiming the role soon, so the handcuff is startable
# for the rival — denial is real, not hypothetical).
BLOCKING_INJURY_STATUSES = HANDCUFF_URGENT_INJURY_STATUSES | {
    "ir",
    "pup",
    "injury_reserve",
}


def _effective_injury(
    espn_status: Optional[str], designation: Optional[str]
) -> str:
    """D2's InjuryDesignation wins over the synced ESPN roster tag when
    present (E1 §3.2 precedence — the official game-status is newer).
    Lowercased to match the status vocabulary."""
    if designation is not None:
        return designation.lower()
    return (espn_status or "").lower()


async def _rival_injured_handcuffs(
    engine,
    espn_league_id: int,
    season: int,
    week: int,
) -> List[dict]:
    """
    The shared join behind both the E5 report and E6's exclusion: every
    (rival team, injured starter, available handcuff) triple. Returns
    rich dicts so blocking_plays() can shape the report and E6 can pull
    just the handcuff player_ids. League must be synced and the user's
    team known (ESPN_MY_TEAMS) — otherwise there are no "rivals" to deny.
    """
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        return []
    my_team = ESPN_MY_TEAMS.get(espn_league_id)
    if my_team is None:
        return []  # no first-person perspective -> no rivals -> no denial
    rival_team_ids = {
        team.espn_team_id
        for team in league.teams
        if team.espn_team_id != my_team
    }
    if not rival_team_ids:
        return []

    pairs = await list_handcuffs(engine)
    if not pairs:
        return []
    pair_by_starter = {pair.starter_name: pair for pair in pairs}

    rosters = await engine.find(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == espn_league_id)
        & (TeamWeekRoster.season == season)
        & (TeamWeekRoster.week == week),
    )
    rival_rosters = [r for r in rosters if r.espn_team_id in rival_team_ids]

    snapshot = await engine.find_one(
        FreeAgentSnapshot,
        (FreeAgentSnapshot.espn_league_id == espn_league_id)
        & (FreeAgentSnapshot.season == season)
        & (FreeAgentSnapshot.week == week),
        sort=(
            query.desc(FreeAgentSnapshot.synced_at),
            query.desc(FreeAgentSnapshot.id),
        ),
    )
    fa_by_name = (
        {entry.player_name: entry for entry in snapshot.entries}
        if snapshot
        else {}
    )

    designations = await engine.find(
        InjuryDesignation,
        (InjuryDesignation.season == season) & (InjuryDesignation.week == week),
    )
    desig_by_name = {d.player_name: d.designation for d in designations}

    results: List[dict] = []
    for roster in rival_rosters:
        for entry in roster.entries:
            pair = pair_by_starter.get(entry.player_name)
            if pair is None:
                continue
            status = _effective_injury(
                entry.injury_status, desig_by_name.get(entry.player_name)
            )
            if status not in BLOCKING_INJURY_STATUSES:
                continue
            fa = fa_by_name.get(pair.handcuff_name)
            if fa is None:
                continue  # handcuff not claimable -> nothing to deny with
            results.append(
                {
                    "starter_name": entry.player_name,
                    "starter_team_id": roster.espn_team_id,
                    "starter_injury_status": status or None,
                    "handcuff_name": fa.player_name,
                    "handcuff_player_id": fa.player_id,
                    "nfl_team": pair.nfl_team,
                    "handcuff_projected_points": fa.projected_points,
                    "handcuff_percent_owned": fa.percent_owned,
                    "position": pair.position,
                }
            )
    return results


async def rival_injured_star_handcuff_ids(
    engine,
    espn_league_id: int,
    season: int,
    week: int,
) -> Set[int]:
    """
    The set of free-agent player_ids E5 claims as blocking plays —
    injured rival starters' handcuffs. E6 imports this and subtracts it
    from its candidate pool (spec §1/§2.3), so the E5/E6 boundary lives
    in one place. Empty when the league isn't synced, the user's team is
    unknown, or no rival is running out an injured starter whose
    handcuff is available.
    """
    triples = await _rival_injured_handcuffs(
        engine, espn_league_id, season, week
    )
    return {t["handcuff_player_id"] for t in triples}


def _blocking_copy(triple: dict, rival_name: str, week: int) -> str:
    """Denial framing: whose workload the handcuff inherits, how hurt the
    starter is, and that grabbing it denies the rival — never points."""
    starter = triple["starter_name"]
    handcuff = triple["handcuff_name"]
    status = triple["starter_injury_status"] or "questionable"
    team = f" ({triple['nfl_team']})" if triple["nfl_team"] else ""
    return (
        f"{starter}{team} is {status} for week {week} and {handcuff} is the "
        f"direct backup sitting on waivers — claiming him denies {rival_name} "
        "the insurance. Pure denial: you don't need the points, you need "
        "them off the board."
    )


async def blocking_plays(
    engine,
    espn_league_id: int,
    season: int,
    week: int,
) -> dict:
    """
    On-demand E5 blocking report: every rival injured-star handcuff that
    is claimable right now, framed as denial. Cheap Mongo join — computed
    when called, not stored (contrast E6's stored weekly scan). Inherits
    the cached-only constraint: no fetches, no data_sources import.
    """
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        return {"week": week, "entries": [], "note": "league not synced"}
    my_team = ESPN_MY_TEAMS.get(espn_league_id)
    if my_team is None:
        # no first-person perspective: denial is meaningless without a rival
        return {
            "week": week,
            "entries": [],
            "note": "no my-team for this league (ESPN_MY_TEAMS)",
        }

    triples = await _rival_injured_handcuffs(
        engine, espn_league_id, season, week
    )
    team_names = {team.espn_team_id: team.name for team in league.teams}
    entries = []
    for triple in triples:
        rival_name = team_names.get(
            triple["starter_team_id"], f"team {triple['starter_team_id']}"
        )
        entries.append(
            {
                "starter_name": triple["starter_name"],
                "starter_team_id": triple["starter_team_id"],
                "starter_injury_status": triple["starter_injury_status"],
                "handcuff_name": triple["handcuff_name"],
                "handcuff_player_id": triple["handcuff_player_id"],
                "nfl_team": triple["nfl_team"],
                "position": triple["position"],
                "handcuff_projected_points": triple["handcuff_projected_points"],
                "handcuff_percent_owned": triple["handcuff_percent_owned"],
                "copy": _blocking_copy(triple, rival_name, week),
            }
        )
    # stable order: most-injured starter first (out before doubtful before
    # questionable), then by starter name
    severity = {"out": 0, "ir": 0, "injury_reserve": 0, "pup": 0,
                "doubtful": 1, "questionable": 2}
    entries.sort(
        key=lambda e: (
            severity.get((e["starter_injury_status"] or "").lower(), 3),
            e["starter_name"],
        )
    )
    note = None if entries else "no rival injured-star handcuffs available"
    return {"week": week, "entries": entries, "note": note}
