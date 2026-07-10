# -*- coding: utf-8 -*-
"""
ESPN RANKINGS/ADP ADAPTER

The league-INDEPENDENT half of ESPN access: public ADP, draft ranks, and
season projections via the kona_player_info view. This is a direct HTTP
call because the espn-api library exposes no ADP field (verified in
Addendum A); everything league-scoped (draft history, live drafts) goes
through espn-api instead, in the Phase 3 ingester.

No cookies needed — this is the same public feed ESPN's own draft lobby
uses. Endpoint shapes are unofficial and can drift; the adapter is
deliberately thin so a drift is a one-file fix.
"""
import json
from typing import List, Optional

from .base import BaseSourceAdapter, SourceFetchError, SourceRecord

KONA_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leaguedefaults/{defaults_id}"
)

# ESPN's default-league ids per scoring format (community-documented; the
# half-PPR default has no stable public id, so it reuses PPR ranks)
LEAGUE_DEFAULTS_IDS = {"standard": 1, "ppr": 3, "half_ppr": 3}
RANK_TYPES = {"standard": "STANDARD", "ppr": "PPR", "half_ppr": "PPR"}

POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}

PRO_TEAM_IDS = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA",
    27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}

PLAYER_LIMIT = 1500


def _season_projection(player: dict, season: int) -> Optional[float]:
    """statSourceId 1 = projection, statSplitTypeId 0 = full season"""
    for stat in player.get("stats", []):
        if (
            stat.get("seasonId") == season
            and stat.get("statSourceId") == 1
            and stat.get("statSplitTypeId") == 0
        ):
            return stat.get("appliedTotal")
    return None


class EspnRankingsAdapter(BaseSourceAdapter):
    source_name = "espn"
    min_request_interval_seconds = 2.0

    async def fetch(self, season: int, scoring_format: str) -> List[SourceRecord]:
        defaults_id = LEAGUE_DEFAULTS_IDS.get(scoring_format)
        if defaults_id is None:
            raise SourceFetchError(
                f"espn: unsupported scoring format '{scoring_format}'"
            )
        rank_type = RANK_TYPES[scoring_format]
        fantasy_filter = {
            "players": {
                "limit": PLAYER_LIMIT,
                "sortDraftRanks": {
                    "sortPriority": 100,
                    "sortAsc": True,
                    "value": rank_type,
                },
            }
        }
        response = await self._get(
            KONA_URL.format(season=season, defaults_id=defaults_id),
            params={"view": "kona_player_info"},
            headers={"X-Fantasy-Filter": json.dumps(fantasy_filter)},
        )
        payload = response.json()
        records = []
        for entry in payload.get("players", []):
            player = entry.get("player") or {}
            position = POSITION_IDS.get(player.get("defaultPositionId"))
            if position is None or not player.get("fullName"):
                continue
            ownership = player.get("ownership") or {}
            draft_ranks = player.get("draftRanksByRankType") or {}
            rank_info = draft_ranks.get(rank_type) or {}
            adp = ownership.get("averageDraftPosition") or None
            rank = rank_info.get("rank")
            projection = _season_projection(player, season)
            if adp is None and rank is None and projection is None:
                continue
            records.append(
                SourceRecord(
                    raw_name=player["fullName"],  # defenses: "Cowboys D/ST"
                    position=position,
                    nfl_team=PRO_TEAM_IDS.get(player.get("proTeamId")),
                    rank=rank,
                    adp=adp,
                    projection=projection,
                    extra={"espn_player_id": player.get("id")},
                )
            )
        if not records:
            raise SourceFetchError("espn: response contained no usable players")
        return records
