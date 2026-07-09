# -*- coding: utf-8 -*-
"""
SLEEPER ADAPTER (crowd-sourced ADP + projections)

Ranking-blend source only — owner tendency profiling is ESPN-exclusive
(see docs/ARCHITECTURE_REVIEW.md Addendum A). Uses Sleeper's projections
endpoint, which carries both format-specific ADP (adp_std/adp_half_ppr/
adp_ppr) and season-long point projections (pts_*) per player. No auth,
no key; the docs ask for well under 1000 calls/minute, and this fetch is
a single request.
"""
from typing import List

from .base import BaseSourceAdapter, SourceFetchError, SourceRecord

PROJECTIONS_URL = "https://api.sleeper.com/projections/nfl/{season}"

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]

ADP_KEYS = {"standard": "adp_std", "half_ppr": "adp_half_ppr", "ppr": "adp_ppr"}
PTS_KEYS = {"standard": "pts_std", "half_ppr": "pts_half_ppr", "ppr": "pts_ppr"}


class SleeperAdapter(BaseSourceAdapter):
    source_name = "sleeper"
    min_request_interval_seconds = 1.0

    async def fetch(self, season: int, scoring_format: str) -> List[SourceRecord]:
        if scoring_format not in ADP_KEYS:
            raise SourceFetchError(
                f"sleeper: unsupported scoring format '{scoring_format}'"
            )
        response = await self._get(
            PROJECTIONS_URL.format(season=season),
            params={
                "season_type": "regular",
                "position[]": POSITIONS,
                "order_by": ADP_KEYS[scoring_format],
            },
        )
        records = []
        for row in response.json():
            player = row.get("player") or {}
            stats = row.get("stats") or {}
            position = player.get("position")
            if position not in POSITIONS:
                continue
            # Sleeper reports 0/absent ADP for players nobody drafts
            adp = stats.get(ADP_KEYS[scoring_format]) or None
            projection = stats.get(PTS_KEYS[scoring_format])
            if adp is None and projection is None:
                continue  # inactive/irrelevant player rows
            # Defenses come as first_name=city, last_name=nickname
            name = f"{player.get('first_name', '')} {player.get('last_name', '')}"
            records.append(
                SourceRecord(
                    raw_name=name.strip(),
                    position=position,
                    nfl_team=player.get("team"),
                    adp=adp,
                    projection=projection,
                    extra={"sleeper_player_id": row.get("player_id")},
                )
            )
        if not records:
            raise SourceFetchError("sleeper: response contained no usable players")
        return records
