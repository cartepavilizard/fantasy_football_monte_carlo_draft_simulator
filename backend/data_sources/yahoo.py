# -*- coding: utf-8 -*-
"""
YAHOO ADAPTER (official Fantasy Sports API: draft-analysis ADP)

Uses the sanctioned OAuth2 path exclusively — Yahoo pages are never
scraped (aggressive bot detection; worst-value scraping target, per the
review). Requires a one-time app registration; at runtime the adapter
exchanges the long-lived refresh token for an access token and pages
through the player collection with the draft_analysis subresource
(average_pick, percent_drafted).

Yahoo's JSON is idiosyncratic: entities arrive as lists of one-key dicts
nested under numeric string keys. The parser tolerates that shape rather
than assuming exact indexes.
"""
from typing import List, Optional

from models.config import (
    YAHOO_CLIENT_ID,
    YAHOO_CLIENT_SECRET,
    YAHOO_REFRESH_TOKEN,
)

from .base import BaseSourceAdapter, SourceFetchError, SourceRecord

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
GAMES_URL = (
    "https://fantasysports.yahooapis.com/fantasy/v2/games;game_codes=nfl"
    ";seasons={season}"
)
PLAYERS_URL = (
    "https://fantasysports.yahooapis.com/fantasy/v2/game/{game_key}/players"
    ";out=draft_analysis;sort=AR;start={start};count={count}"
)

PAGE_SIZE = 25
MAX_PLAYERS = 400  # ~16 pages covers every draftable player

POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def _merge_fragments(fragments) -> dict:
    """Yahoo entities are lists of one-key dicts (or nested lists); merge"""
    merged = {}
    stack = [fragments]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            merged.update(item)
        elif isinstance(item, list):
            stack.extend(item)
    return merged


def _numbered_entries(container: dict, key: str):
    """Yield container['0'][key], container['1'][key], ... in order"""
    for index in sorted(
        (k for k in container if k.isdigit()), key=int
    ):
        entry = container[index]
        if isinstance(entry, dict) and key in entry:
            yield entry[key]


class YahooAdapter(BaseSourceAdapter):
    source_name = "yahoo"
    min_request_interval_seconds = 1.5

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        refresh_token: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.client_id = client_id or YAHOO_CLIENT_ID
        self.client_secret = client_secret or YAHOO_CLIENT_SECRET
        self.refresh_token = refresh_token or YAHOO_REFRESH_TOKEN

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    async def _access_token(self) -> str:
        if not self.configured:
            raise SourceFetchError(
                "yahoo: OAuth credentials not configured "
                "(YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REFRESH_TOKEN)"
            )
        response = await self.transport.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
                "redirect_uri": "oob",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if not response.ok:
            raise SourceFetchError(
                f"yahoo: token refresh failed ({response.status_code})"
            )
        token = response.json().get("access_token")
        if not token:
            raise SourceFetchError("yahoo: token response had no access_token")
        return token

    async def _game_key(self, season: int, headers: dict) -> str:
        response = await self._get(
            GAMES_URL.format(season=season),
            params={"format": "json"},
            headers=headers,
        )
        games = (response.json().get("fantasy_content") or {}).get("games") or {}
        for game in _numbered_entries(games, "game"):
            merged = _merge_fragments(game)
            if merged.get("game_key"):
                return str(merged["game_key"])
        raise SourceFetchError(f"yahoo: no NFL game found for season {season}")

    async def fetch(self, season: int, scoring_format: str) -> List[SourceRecord]:
        # Yahoo ADP is not scoring-format-specific; format is accepted for
        # interface parity and ignored
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        game_key = await self._game_key(season, headers)

        records = []
        for start in range(0, MAX_PLAYERS, PAGE_SIZE):
            response = await self._get(
                PLAYERS_URL.format(
                    game_key=game_key, start=start, count=PAGE_SIZE
                ),
                params={"format": "json"},
                headers=headers,
            )
            page = self._parse_players_page(response.json())
            records.extend(page)
            if len(page) < PAGE_SIZE:
                break
        usable = [record for record in records if record.adp is not None]
        if not usable:
            raise SourceFetchError("yahoo: response contained no usable players")
        return usable

    def _parse_players_page(self, payload: dict) -> List[SourceRecord]:
        content = payload.get("fantasy_content") or {}
        game = content.get("game") or []
        players_container = {}
        for fragment in game if isinstance(game, list) else [game]:
            if isinstance(fragment, dict) and "players" in fragment:
                players_container = fragment["players"] or {}
                break
        records = []
        for player in _numbered_entries(players_container, "player"):
            attributes = _merge_fragments(
                player[0] if isinstance(player, list) and player else player
            )
            analysis = {}
            for fragment in player[1:] if isinstance(player, list) else []:
                if isinstance(fragment, dict) and "draft_analysis" in fragment:
                    analysis = _merge_fragments(fragment["draft_analysis"])
            position = attributes.get("display_position")
            name = (attributes.get("name") or {}).get("full")
            if position not in POSITIONS or not name:
                continue
            average_pick = analysis.get("average_pick")
            try:
                adp = float(average_pick)
            except (TypeError, ValueError):  # Yahoo uses "-" for undrafted
                adp = None
            records.append(
                SourceRecord(
                    raw_name=name,
                    position=position,
                    nfl_team=attributes.get("editorial_team_abbr"),
                    adp=adp,
                    extra={"percent_drafted": analysis.get("percent_drafted")},
                )
            )
        return records
