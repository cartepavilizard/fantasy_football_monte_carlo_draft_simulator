# -*- coding: utf-8 -*-
"""
HOMER CHECK METHODOLOGY (Phase A, task A6 — reused in-season by C9)

When the engine suggests a player from the user's team (HOMER_TEAM,
default SEA), it attaches a neutral side-by-side value comparison
against the top alternatives at the same position, so the user can see
whether the pick is value or fandom. One methodology, three call sites:
draft suggestions (here), waiver adds and trade pieces (C9 passes a
free-agent or roster pool to the same function).

Methodology rules — these are the contract, keep them stable:

- The comparison is deliberately TAG-BLIND: alternatives rank by raw
  projected points, with no sleeper boost and no my_guy tie-break. The
  homer check is the debiasing instrument, so it must show the
  untagged truth even when the homer pick is also a my_guy. The single
  exception is `avoid`, whose players are excluded because they are
  not real options for this user (consistent with A4). Tags are still
  *displayed* on comparison rows for transparency.
- The output is facts plus signed gaps and a factual note. There is NO
  recommendation field, by design — the check informs, never directs.
- Gap sign convention: positive favors the homer pick, negative favors
  the best alternative.
    projection_gap = homer projection - best alternative projection
    market_gap     = best alternative consensus rank - homer consensus
                     rank (rank: lower is better), None if either rank
                     is missing
- adp_vs_pick = adp - pick_number for each player (negative = the
  market expected them gone already; large positive = reach). None
  when pick_number or adp is unavailable (in-season call sites have no
  pick number).

Spec for the display task (A6's cheaper half): render `suggested` and
`alternatives` as one table — columns projection / consensus rank /
ADP vs. pick / tier, tag markers on names — with `note` as the caption.
Draft scope reads MonteCarloSimulationResult.homer_checks (position →
HomerCheck), present only for positions whose suggested pick is a
HOMER_TEAM player.
"""
from .config import HOMER_TEAM
from pydantic import BaseModel
from typing import List, Union

# How many non-homer-team alternatives to show
HOMER_ALTERNATIVES_LIMIT = 3


class ComparisonPlayer(BaseModel):
    """One row of the side-by-side comparison"""

    name: str
    nfl_team: str
    projected_points: float
    consensus_rank: Union[float, None] = None
    adp: Union[float, None] = None
    adp_vs_pick: Union[float, None] = None
    tier: Union[int, None] = None
    tag: Union[str, None] = None  # displayed for transparency, never ranked on


class HomerCheck(BaseModel):
    """Neutral comparison of a homer-team pick vs. the top alternatives"""

    position: str
    homer_team: str
    pick_number: Union[int, None] = None  # None at in-season call sites
    suggested: ComparisonPlayer
    alternatives: List[ComparisonPlayer]
    projection_gap: float  # positive favors the homer pick
    market_gap: Union[float, None] = None  # positive favors the homer pick
    note: str  # factual summary; no recommendation, by design


def _comparison_player(player, year: str, pick_number: Union[int, None]) -> ComparisonPlayer:
    return ComparisonPlayer(
        name=player.name,
        nfl_team=player.nfl_team,
        projected_points=player.points[year].projected_points,
        consensus_rank=player.consensus_rank,
        adp=player.adp,
        adp_vs_pick=(
            round(player.adp - pick_number, 2)
            if player.adp is not None and pick_number is not None
            else None
        ),
        tier=player.tier,
        tag=player.tag,
    )


def homer_check(
    candidate,
    pool: list,
    pick_number: Union[int, None],
    year: str,
    homer_team: str = HOMER_TEAM,
    limit: int = HOMER_ALTERNATIVES_LIMIT,
) -> Union[HomerCheck, None]:
    """
    Build the neutral comparison for one candidate against a pool of
    same-position players. Returns None when the candidate is not a
    homer-team player or no alternatives exist (nothing to compare)
    """
    if candidate.nfl_team.upper() != homer_team.upper():
        return None
    alternatives = sorted(
        [
            player
            for player in pool
            if not player.drafted
            and player.name != candidate.name
            and player.nfl_team.upper() != homer_team.upper()
            and player.tag != "avoid"
        ],
        key=lambda player: player.points[year].projected_points,
        reverse=True,
    )[:limit]
    if not alternatives:
        return None

    suggested = _comparison_player(candidate, year, pick_number)
    rows = [_comparison_player(player, year, pick_number) for player in alternatives]
    best = rows[0]

    projection_gap = round(suggested.projected_points - best.projected_points, 2)
    market_gap = (
        round(best.consensus_rank - suggested.consensus_rank, 2)
        if suggested.consensus_rank is not None and best.consensus_rank is not None
        else None
    )

    direction = "above" if projection_gap >= 0 else "below"
    note = (
        f"{suggested.name} projects {abs(projection_gap):.1f} pts {direction}"
        f" the top non-{homer_team} alternative ({best.name})."
    )
    if market_gap is not None and market_gap != 0:
        market_direction = "ahead of" if market_gap > 0 else "behind"
        note += (
            f" Market consensus ranks {suggested.name}"
            f" {abs(market_gap):.0f} spots {market_direction} {best.name}."
        )

    return HomerCheck(
        position=candidate.position,
        homer_team=homer_team.upper(),
        pick_number=pick_number,
        suggested=suggested,
        alternatives=rows,
        projection_gap=projection_gap,
        market_gap=market_gap,
        note=note,
    )
