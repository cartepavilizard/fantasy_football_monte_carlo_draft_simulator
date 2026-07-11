# -*- coding: utf-8 -*-
"""
K/DST STREAMING RECOMMENDATIONS (PHASE C, TASK C3)

Weekly rank of available kickers and defenses by matchup: join B1's
free-agent snapshot against C2's matchup-strength table. Cached-only by
construction — FreeAgentSnapshot and defense_position_strength() both
read straight from Mongo — so this module inherits inseason_api.py's
constraint without importing data_sources.

METHODOLOGY: filter the latest FreeAgentSnapshot to K/DST, look up each
player's week opponent via opponent_map(), then rank by
matchup_adjusted(projected_points, multiplier) — C2's capped tilt, not
a re-projection — tie-breaking by the raw multiplier (between two rows
tied on tilted points, the tougher matchup call is the more informative
signal). Each row carries its matchup entry (multiplier, observed
ratio, weeks sampled, confidence, defense rank) as context, per C8's
process-over-results framing: the ranking is legible, not a black box.

Note on position semantics: defense_position_strength()["DST"] is
"fantasy points allowed to opposing DSTs", which is really a measure of
the OPPOSING OFFENSE's turnover/sack proneness — so looking it up by
the streaming DST's opponent is exactly right (a bad offense = a soft
matchup for the DST facing it). Same read for K against the opposing
defense's field-goal generosity.

C9 (homer check): any HOMER_TEAM (Seahawks) player in the ranked list
gets homer_check() run against the same-position pool of other free
agents. FreeAgentEntry doesn't carry the attribute shape homer_check()
reads (points[year].projected_points, drafted, tag, consensus_rank,
adp, tier) — free agents have none of the draft-scope concepts, so
FreeAgentHomerCandidate adapts minimally: drafted=False (a free agent
is never drafted), tag/consensus_rank/adp/tier=None (no in-season
equivalent), pick_number=None at the call site (no draft in progress).
This adapter is public: models/handcuffs.py (C9's second in-season call
site) imports it unchanged rather than reimplementing the same shape.
"""
from typing import Optional

from odmantic import query

from .config import HOMER_TEAM
from .homer import homer_check
from .inseason import FreeAgentSnapshot
from .matchup_strength import defense_position_strength, matchup_adjusted, opponent_map, strength_for

STREAMING_POSITIONS = ("K", "DST")


class FreeAgentHomerPoints:
    def __init__(self, projected_points: float):
        self.projected_points = projected_points


class FreeAgentHomerCandidate:
    """Adapts a FreeAgentEntry to the attribute shape homer_check() reads"""

    def __init__(self, entry, year: str):
        self.name = entry.player_name
        self.nfl_team = entry.nfl_team or ""
        self.position = entry.position or ""
        self.drafted = False  # a free agent is, definitionally, undrafted
        self.tag = None  # no tag system in-season
        self.consensus_rank = None  # no draft-scope consensus rank in-season
        self.adp = None  # no ADP in-season
        self.tier = None  # no tier system in-season
        self.points = {year: FreeAgentHomerPoints(entry.projected_points or 0.0)}


async def streaming_recommendations(
    engine,
    espn_league_id: int,
    season: int,
    week: int,
    strength: Optional[dict] = None,
    opponents: Optional[dict] = None,
) -> dict:
    """
    The ranked K/DST streaming list for one league-week: latest synced
    free-agent pool, C2 matchup context, C9 homer checks. Returns
    {"week": week, "recommendations": [...]} — empty list when no
    free-agent snapshot has synced yet for this league-week. strength/
    opponents are injectable (same seam as optimize_lineup()) so tests
    can hand-build a matchup table instead of seeding real actuals.
    """
    snapshot = await engine.find_one(
        FreeAgentSnapshot,
        (FreeAgentSnapshot.espn_league_id == espn_league_id)
        & (FreeAgentSnapshot.season == season)
        & (FreeAgentSnapshot.week == week),
        sort=(query.desc(FreeAgentSnapshot.synced_at), query.desc(FreeAgentSnapshot.id)),
    )
    candidates = [
        entry
        for entry in (snapshot.entries if snapshot else [])
        if (entry.position or "").upper() in STREAMING_POSITIONS
    ]
    if not candidates:
        return {"week": week, "recommendations": []}

    if strength is None:
        strength = await defense_position_strength(engine, season)
    if opponents is None:
        opponents = await opponent_map(engine, season)

    rows = []
    for entry in candidates:
        opponent = opponents.get((entry.nfl_team, week)) if entry.nfl_team else None
        matchup = strength_for(strength, entry.position, opponent)
        adjusted = matchup_adjusted(entry.projected_points, matchup["multiplier"])
        rows.append(
            {
                "player_id": entry.player_id,
                "player_name": entry.player_name,
                "position": (entry.position or "").upper(),
                "nfl_team": entry.nfl_team,
                "opponent": opponent,
                "projected_points": entry.projected_points,
                "matchup_adjusted_points": adjusted,
                "matchup": matchup,
                "homer_check": None,
            }
        )

    # rank by matchup-adjusted points, tie-break by the raw multiplier
    rows.sort(
        key=lambda row: (
            row["matchup_adjusted_points"]
            if row["matchup_adjusted_points"] is not None
            else float("-inf"),
            row["matchup"]["multiplier"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    year = str(season)
    for row in rows:
        if (row["nfl_team"] or "").upper() != HOMER_TEAM.upper():
            continue
        pool = [
            FreeAgentHomerCandidate(entry, year)
            for entry in candidates
            if (entry.position or "").upper() == row["position"]
            and entry.player_name != row["player_name"]
        ]
        candidate_entry = next(
            entry for entry in candidates if entry.player_name == row["player_name"]
        )
        check = homer_check(
            FreeAgentHomerCandidate(candidate_entry, year),
            pool,
            pick_number=None,
            year=year,
        )
        row["homer_check"] = check.model_dump() if check else None

    return {"week": week, "recommendations": rows}
