# -*- coding: utf-8 -*-
"""
TAG EFFECTS IN THE SUGGESTION ENGINE (Phase A, task A4)

Player tags (A3) shape what the engine suggests without distorting the
Monte Carlo itself. The rules, and where each one applies:

- avoid   — filtered out of every suggestion regardless of projection.
            Also a hard behavioral constraint on the simulator team's
            own picks (live auto-pick and its future picks inside
            rollouts): the user genuinely won't draft these players.
            Opponents still draft them normally, so availability
            predictions stay honest.
- my_guy  — wins ties: if a my_guy's value is within
            max(MY_GUY_TIE_PERCENT of the best candidate's value,
            MY_GUY_TIE_FLOOR_POINTS) of the best, the my_guy is
            suggested instead. Suggestion surface only.
- sleeper — late-round consideration boost: a sleeper's value is
            multiplied by (1 + boost) when choosing a candidate, where
            boost is 0 through the first SLEEPER_BOOST_START fraction
            of the draft and ramps linearly to SLEEPER_MAX_BOOST at the
            final round (upside matters more than floor late).
            Selection only — the points a drafted sleeper contributes
            in simulation remain the raw projection, so tag enthusiasm
            can never inflate a simulated outcome.

Everything here is pure; app.py wires it into monte_carlo_draft
(candidate choice + the `suggested` map on the result) and simulate_pick
(the simulator team's avoid constraint).
"""
from .config import (
    DRAFT_YEAR,
    MY_GUY_TIE_FLOOR_POINTS,
    MY_GUY_TIE_PERCENT,
    SLEEPER_BOOST_START,
    SLEEPER_MAX_BOOST,
)
from pydantic import BaseModel
from typing import Tuple, Union


class SuggestedPick(BaseModel):
    """The player the engine would take at a position, and why"""

    name: str
    tag: Union[str, None] = None
    reason: str = ""  # empty when the raw best projection won on merit


def sleeper_boost(round_num: int, total_rounds: int) -> float:
    """
    The consideration multiplier bonus for sleeper-tagged players at a
    point in the draft: zero through SLEEPER_BOOST_START, then a linear
    ramp to SLEEPER_MAX_BOOST at the final round
    """
    if total_rounds <= 0:
        return 0.0
    progress = round_num / total_rounds
    if progress <= SLEEPER_BOOST_START:
        return 0.0
    ramp = (progress - SLEEPER_BOOST_START) / (1 - SLEEPER_BOOST_START)
    return SLEEPER_MAX_BOOST * min(ramp, 1.0)


def suggest_candidate(
    position_players: list,
    round_num: int,
    total_rounds: int,
    year: str = str(DRAFT_YEAR),
) -> Tuple[Union[object, None], str]:
    """
    The player the engine suggests at one position for the current pick,
    applying all three tag effects. Returns (player, reason); player is
    None when nothing remains (or everything remaining is avoid-tagged)
    """
    available = [
        player
        for player in position_players
        if not player.drafted and player.tag != "avoid"
    ]
    if not available:
        return None, ""

    boost = sleeper_boost(round_num, total_rounds)

    def value(player) -> float:
        projected = player.points[year].projected_points
        if player.tag == "sleeper" and boost > 0:
            return projected * (1 + boost)
        return projected

    best = max(available, key=value)
    raw_best = max(
        available, key=lambda player: player.points[year].projected_points
    )

    # my_guy tie-break: the best my_guy within the closeness margin of
    # the best candidate wins (margins compare boosted values, so the
    # tie-break and the boost live in one consistent value space)
    if best.tag != "my_guy":
        margin = max(MY_GUY_TIE_PERCENT * value(best), MY_GUY_TIE_FLOOR_POINTS)
        my_guys = [
            player
            for player in available
            if player.tag == "my_guy" and value(best) - value(player) <= margin
        ]
        if my_guys:
            choice = max(my_guys, key=value)
            return choice, (
                f"my_guy tie-break: within {value(best) - value(choice):.1f}"
                f" pts of {best.name}"
            )

    if best is not raw_best and best.tag == "sleeper":
        return best, (
            f"sleeper boost +{boost:.0%} over {raw_best.name}"
            f" (round {round_num} of {total_rounds})"
        )
    return best, ""
