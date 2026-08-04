# -*- coding: utf-8 -*-
"""
PRESEASON STRENGTH OF SCHEDULE INGESTION (PHASE H, TASK H8)

The ingestion half of the draft-time SOS signal; models/preseason_sos.py
is the cached-only read half. Reuses nflverse's public player_stats CSV
(same file C4's NflverseUsageAdapter already fetches) rather than a new
source, because that CSV carries `opponent_team` and `fantasy_points_ppr`
for every offensive player-week of a completed NFL season — real
full-league data, not the roster-subsample C2 draws on in-season, and
free/unauthenticated like the rest of nflverse's releases.

Methodology: for each (week, position) of the completed source_season,
sum fantasy_points_ppr across every player whose opponent that week was
a given defense — that IS "points allowed to that position by that
defense" for that week. ratio(defense) = that sum / the mean across all
defenses sampled that week (same normalization C2 uses, so a defense
that split a bye differently than another does not skew the mean).
Ratios average across weeks, then the observed multiplier is damped
toward neutral by PRESEASON_SOS_DAMPING (see models/config.py — a full
season is a reliable measurement of LAST year's defense, but personnel
turn over, so it is a weaker prior for the season being drafted than
C2's same-season signal is), and clamped to the same MULTIPLIER_CLAMP
matchup_strength.py uses so the two signals stay on one scale.

Runs only from POST /rankings/preseason_sos/refresh — never from a
read path — so cached-only modules never import this.
"""
import csv
import io
from collections import defaultdict
from typing import List, Optional

from models.config import PRESEASON_SOS_DAMPING
from models.matchup_strength import (
    MIN_DEFENSES_SAMPLED,
    MULTIPLIER_CLAMP,
    RATIO_CLAMP,
    STRENGTH_POSITIONS,
    _clamp,
)
from models.preseason_sos import PreseasonDefenseStrength

from .base import SourceFetchError
from .nfl_teams import normalize_team_abbrev
from .ratelimit import RateLimiter
from .transport import HttpxTransport, Transport

# nflverse's per-season `player_stats_{season}.csv` release (what C4's
# NflverseUsageAdapter uses) was DEPRECATED 2025-08-01 in favor of
# `stats_player_week_{season}.csv` -- verified live: player_stats_2025
# 404s, stats_player_week_2025 serves real 2025 data with the same
# opponent_team/fantasy_points_ppr columns this module needs. Ingesting
# a real prior season is H8's whole point, so this module uses the
# current release rather than inheriting C4's now-stale one.
PLAYER_STATS_WEEK_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)


class PreseasonSosAdapter:
    """Fetches one completed season's player_stats CSV; injectable Transport for tests"""

    min_request_interval_seconds = 1.0

    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or HttpxTransport()
        self._rate_limiter = RateLimiter(self.min_request_interval_seconds)

    async def fetch_points_allowed(self, source_season: int) -> List[dict]:
        """
        [{week, position, defense, points}] — one row per offensive
        player-week, points credited to the opponent it was scored
        against. Non-REG weeks, unparseable rows, and positions outside
        STRENGTH_POSITIONS are dropped.
        """
        await self._rate_limiter.wait()
        url = PLAYER_STATS_WEEK_URL.format(season=source_season)
        response = await self.transport.get(url)
        if not response.ok:
            raise SourceFetchError(
                f"nflverse: GET {url} returned {response.status_code}"
            )
        rows = []
        for row in csv.DictReader(io.StringIO(response.text)):
            if (row.get("season_type") or "") != "REG":
                continue
            position = (row.get("position") or "").strip()
            if position not in STRENGTH_POSITIONS:
                continue
            defense = normalize_team_abbrev(row.get("opponent_team"))
            if not defense:
                continue
            try:
                week = int(float(row.get("week")))
                points = float(row.get("fantasy_points_ppr"))
            except (TypeError, ValueError):
                continue
            rows.append(
                {"week": week, "position": position, "defense": defense, "points": points}
            )
        return rows


async def ingest_preseason_sos(
    engine,
    target_season: int,
    source_season: Optional[int] = None,
    adapter: Optional[PreseasonSosAdapter] = None,
) -> dict:
    """
    One ingestion pass: fetch source_season's (default target_season - 1)
    real points-allowed, compute damped multipliers per (position,
    defense), and replace the stored PreseasonDefenseStrength rows for
    target_season. A fetch failure is reported, not raised — nothing is
    written, and the prior stored table (if any) is left in place.
    """
    source_season = target_season - 1 if source_season is None else source_season
    adapter = adapter or PreseasonSosAdapter()
    summary = {
        "target_season": target_season,
        "source_season": source_season,
        "positions": {},
    }

    try:
        rows = await adapter.fetch_points_allowed(source_season)
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        return summary

    # (week, position) -> defense -> points allowed that week
    samples = defaultdict(lambda: defaultdict(float))
    for row in rows:
        samples[(row["week"], row["position"])][row["defense"]] += row["points"]

    # position -> defense -> [ratio per week]
    ratios = defaultdict(lambda: defaultdict(list))
    for (_week, position), by_defense in samples.items():
        if len(by_defense) < MIN_DEFENSES_SAMPLED:
            continue
        mean_points = sum(by_defense.values()) / len(by_defense)
        if mean_points <= 0:
            continue
        for defense, points in by_defense.items():
            ratios[position][defense].append(_clamp(points / mean_points, RATIO_CLAMP))

    to_save = []
    for position in STRENGTH_POSITIONS:
        by_defense = {}
        for defense, values in ratios[position].items():
            observed = sum(values) / len(values)
            damped = 1.0 + PRESEASON_SOS_DAMPING * (observed - 1.0)
            by_defense[defense] = {
                "multiplier": _clamp(damped, MULTIPLIER_CLAMP),
                "observed_ratio": observed,
                "games_sampled": len(values),
            }
        ordered = sorted(
            by_defense.items(), key=lambda item: item[1]["multiplier"], reverse=True
        )
        for rank, (defense, entry) in enumerate(ordered, start=1):
            to_save.append(
                PreseasonDefenseStrength(
                    target_season=target_season,
                    source_season=source_season,
                    position=position,
                    defense=defense,
                    multiplier=round(entry["multiplier"], 4),
                    observed_ratio=round(entry["observed_ratio"], 4),
                    games_sampled=entry["games_sampled"],
                    rank=rank,
                )
            )
        summary["positions"][position] = len(by_defense)

    await engine.get_collection(PreseasonDefenseStrength).delete_many(
        {"target_season": target_season}
    )
    if to_save:
        await engine.save_all(to_save)
    summary["rows_saved"] = len(to_save)
    return summary
