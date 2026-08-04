# -*- coding: utf-8 -*-
"""
PRESEASON STRENGTH OF SCHEDULE (PHASE H, TASK H8)

The one gap C2 (matchup_strength.py) and C5 (playoff_sos.py) leave: both
are structurally neutral before week 1 of the season being drafted, so
neither says anything at draft time. This module is the read path for a
draft-time prior — last completed season's real, full-NFL-season
defense-vs-position points-allowed ratio, ingested from nflverse (see
data_sources/preseason_sos.py) and damped toward neutral before being
stored, because a full roster/coaching turnover between seasons makes
last year's defense a genuinely weaker signal than C2's same-season one.
That damping (PRESEASON_SOS_DAMPING) is applied once, at ingestion time;
this module only reads what was stored.

Mongo-only by construction — no data_sources import here — so it
inherits the cached-only constraint inseason_api.py enforces structurally
(see test_cached_only_modules_never_import_data_sources) even though
this module isn't in that guarded list itself; the ingestion adapter
that talks to nflverse lives in data_sources/preseason_sos.py and is
invoked only from POST /rankings/preseason_sos/refresh, never from a
read.

Same rank convention as C2/C5: rank 1 = allows the most points
(softest matchup, best for the offense player).
"""
import datetime
from typing import Optional

from odmantic import Field as ODField
from odmantic import Model


class PreseasonDefenseStrength(Model):
    """
    One (target_season, position, defense) row. target_season is the
    season being drafted; source_season is the completed season the
    ratio was measured from (target_season - 1 by default, but kept
    explicit rather than assumed since not every target season has a
    contiguous prior one synced).
    """

    model_config = {"collection": "preseason_defense_strength"}

    target_season: int
    source_season: int
    position: str
    defense: str
    multiplier: float
    observed_ratio: float
    games_sampled: int
    rank: int
    computed_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


async def preseason_defense_strength(engine, target_season: int) -> dict:
    """
    The full table for a target season: position -> defense -> {
    multiplier, observed_ratio, games_sampled, rank }. Empty (with a
    `note`) if POST /rankings/preseason_sos/refresh has never run for
    this target_season.
    """
    rows = await engine.find(
        PreseasonDefenseStrength,
        PreseasonDefenseStrength.target_season == target_season,
    )
    positions: dict = {}
    for row in rows:
        positions.setdefault(row.position, {})[row.defense] = {
            "multiplier": row.multiplier,
            "observed_ratio": row.observed_ratio,
            "games_sampled": row.games_sampled,
            "rank": row.rank,
        }
    return {
        "target_season": target_season,
        "source_season": rows[0].source_season if rows else None,
        "positions": positions,
        "note": (
            None
            if rows
            else "No preseason SOS ingested yet for this season — "
            "POST /rankings/preseason_sos/refresh first."
        ),
    }


def preseason_sos_for(
    strength: dict, position: Optional[str], defense: Optional[str]
) -> dict:
    """
    One defense-vs-position entry, defaulting to neutral when either
    side is unknown or nothing was ingested — same shape as C2's
    strength_for() so callers can treat the two signals uniformly.
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
            "games_sampled": 0,
            "rank": None,
        }
    return entry
