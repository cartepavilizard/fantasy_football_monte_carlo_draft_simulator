# -*- coding: utf-8 -*-
"""
OPPONENT-VS-POSITION MATCHUP STRENGTH (PHASE C, TASK C2)

How hard or soft each NFL defense is against each fantasy position,
computed entirely from data Phase B already syncs into Mongo — no new
external source, so the cached-only constraint holds for free. Feeds
C1's projection adjustments, C3's K/DST streaming ranks, and C5's
playoff-SOS report, and is shown as context on lineup calls.

METHODOLOGY (the contract — change it deliberately, in one place):

Signal: fantasy points allowed. For every completed week, every synced
league, and every roster entry with actual points, credit those points
"against" the defense that player faced (opponent from the synced NFL
schedule). Rostered players are a biased-low sample of the whole NFL,
but the bias is roughly uniform across defenses — league managers
roster the startable players everywhere — so RATIOS survive: each
(league, week, position) sample is normalized by that sample's own
per-defense mean before anything is aggregated.

    ratio(defense) = points allowed to position / mean over defenses

Cross-league handling: the same NFL week sampled by three leagues is
better COVERAGE of the same games, not three times the evidence. Ratios
are averaged across leagues within a week first, then across weeks —
n counts distinct NFL weeks, never league-weeks.

Small-sample story (September): the observed ratio is shrunk toward
neutral (1.0) with a fixed prior worth MATCHUP_PRIOR_GAMES (default 4)
weeks of evidence:

    multiplier = (n * observed + PRIOR * 1.0) / (n + PRIOR)

So week 1 has no completed weeks -> every multiplier is exactly 1.0
(adjustments structurally off, confidence "none"); week 2 has n=1 ->
an observed 30%-soft defense moves projections ~6%; the data does not
speak at full weight until several real weeks exist. Confidence is
reported alongside so the UI can say "early-season estimate" instead
of pretending.

Guardrails:
- A (league, week, position) sample needs at least MIN_DEFENSES_SAMPLED
  defenses before its ratios count (a two-defense Thursday-only sample
  says nothing about the league-wide mean).
- Per-sample ratios clamp to [0.25, 3.0]; final multipliers clamp to
  [0.7, 1.3]. Matchups tilt projections, they do not rewrite them.
- Only weeks strictly BEFORE a league's latest_scoring_period are
  sampled: the in-progress week's actuals are partial (Thursday-only)
  and would distort the per-week mean.

How much weight (C1's adjustment): ESPN's weekly projections already
price the opponent to some degree, so applying the full multiplier
would double-count the matchup. matchup_adjusted() therefore applies
a TILT: alpha * (multiplier - 1), alpha = MATCHUP_TILT_ALPHA (default
0.5), capped at +/- MATCHUP_MAX_TILT (default 10%). A maximally soft
defense (1.3 after clamps) moves a 20-point projection by +2.0, never
more.

Rank convention: rank 1 = allows the MOST points (softest matchup,
best for the offense player), matching how fantasy-points-against
tables are conventionally read.
"""
import datetime
from collections import defaultdict
from typing import Dict, List, Optional

from .config import (
    MATCHUP_MAX_TILT,
    MATCHUP_PRIOR_GAMES,
    MATCHUP_TILT_ALPHA,
)
from .inseason import InSeasonLeague, ProGame, TeamWeekRoster

# A (league, week, position) sample must cover at least this many
# defenses to say anything about the per-defense mean
MIN_DEFENSES_SAMPLED = 4

# Per-sample and final clamps (see module docstring)
RATIO_CLAMP = (0.25, 3.0)
MULTIPLIER_CLAMP = (0.7, 1.3)

STRENGTH_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]


def _clamp(value: float, bounds) -> float:
    low, high = bounds
    return max(low, min(high, value))


def _confidence(weeks_sampled: int) -> str:
    if weeks_sampled == 0:
        return "none"
    if weeks_sampled < 3:
        return "low"
    if weeks_sampled < 6:
        return "medium"
    return "high"


async def opponent_map(engine, season: int) -> Dict[tuple, str]:
    """{(nfl_team, week) -> opponent} from the synced NFL schedule"""
    games = await engine.find(ProGame, ProGame.season == season)
    opponents = {}
    for game in games:
        opponents[(game.home_team, game.week)] = game.away_team
        opponents[(game.away_team, game.week)] = game.home_team
    return opponents


async def defense_position_strength(
    engine,
    season: int,
    through_week: Optional[int] = None,
    league_ids: Optional[List[int]] = None,
) -> dict:
    """
    The full strength table: position -> defense -> multiplier, observed
    ratio, weeks sampled, rank, confidence. through_week additionally
    caps which completed weeks are sampled (C5's playoff SOS will call
    with specific windows); by default every completed week counts.
    """
    opponents = await opponent_map(engine, season)
    leagues = await engine.find(InSeasonLeague, InSeasonLeague.season == season)
    if league_ids is not None:
        leagues = [
            league for league in leagues if league.espn_league_id in league_ids
        ]

    # position -> defense -> week -> [ratio per league]
    ratios = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    for league in leagues:
        rosters = await engine.find(
            TeamWeekRoster,
            (TeamWeekRoster.espn_league_id == league.espn_league_id)
            & (TeamWeekRoster.season == season),
        )
        # (week, position) -> defense -> points allowed in this league
        samples = defaultdict(lambda: defaultdict(float))
        for roster in rosters:
            week = roster.week
            if week >= league.latest_scoring_period:
                continue  # in-progress week: partial actuals distort means
            if through_week is not None and week > through_week:
                continue
            for entry in roster.entries:
                if (
                    entry.actual_points is None
                    or not entry.position
                    or not entry.nfl_team
                ):
                    continue
                defense = opponents.get((entry.nfl_team, week))
                if defense is None:
                    continue  # bye or unknown schedule
                samples[(week, entry.position)][defense] += entry.actual_points

        for (week, position), by_defense in samples.items():
            if len(by_defense) < MIN_DEFENSES_SAMPLED:
                continue
            mean_points = sum(by_defense.values()) / len(by_defense)
            if mean_points <= 0:
                continue
            for defense, points in by_defense.items():
                ratios[position][defense][week].append(
                    _clamp(points / mean_points, RATIO_CLAMP)
                )

    positions = {}
    for position in STRENGTH_POSITIONS:
        by_defense = {}
        for defense, by_week in ratios[position].items():
            # average across leagues within a week, then across weeks:
            # n is distinct NFL weeks, never league-weeks
            week_means = [
                sum(week_ratios) / len(week_ratios)
                for week_ratios in by_week.values()
            ]
            n = len(week_means)
            observed = sum(week_means) / n
            multiplier = _clamp(
                (n * observed + MATCHUP_PRIOR_GAMES * 1.0)
                / (n + MATCHUP_PRIOR_GAMES),
                MULTIPLIER_CLAMP,
            )
            by_defense[defense] = {
                "multiplier": round(multiplier, 4),
                "observed_ratio": round(observed, 4),
                "weeks_sampled": n,
                "confidence": _confidence(n),
            }
        # rank 1 = softest (allows the most points)
        ordered = sorted(
            by_defense.items(),
            key=lambda item: item[1]["multiplier"],
            reverse=True,
        )
        for rank, (defense, entry) in enumerate(ordered, start=1):
            entry["rank"] = rank
        positions[position] = by_defense

    sampled = any(positions[position] for position in positions)
    return {
        "season": season,
        "through_week": through_week,
        "positions": positions,
        "note": (
            None
            if sampled
            else "No completed weeks synced yet — all matchups are neutral "
            "(multiplier 1.0) until real games are in the books."
        ),
    }


def strength_for(
    strength: dict, position: Optional[str], defense: Optional[str]
) -> dict:
    """
    One defense-vs-position entry from a defense_position_strength()
    table, defaulting to neutral when either side is unknown or the
    defense has no sample yet — the neutral default IS the week-1 story.
    """
    entry = (
        strength["positions"].get(position or "", {}).get(defense or "")
        if strength
        else None
    )
    if entry is None:
        return {
            "multiplier": 1.0,
            "observed_ratio": None,
            "weeks_sampled": 0,
            "confidence": "none",
            "rank": None,
        }
    return entry


def matchup_adjusted(
    base_projection: Optional[float],
    multiplier: float,
    alpha: Optional[float] = None,
    max_tilt: Optional[float] = None,
) -> Optional[float]:
    """
    C1's adjustment: a capped tilt, not a re-projection (ESPN weekly
    projections already partially price the matchup — see docstring)
    """
    if base_projection is None:
        return None
    if alpha is None:
        alpha = MATCHUP_TILT_ALPHA
    if max_tilt is None:
        max_tilt = MATCHUP_MAX_TILT
    tilt = _clamp(alpha * (multiplier - 1.0), (-max_tilt, max_tilt))
    return round(base_projection * (1.0 + tilt), 2)
