# -*- coding: utf-8 -*-
"""
FANTASYPROS ADAPTER (expert consensus rankings + tiers)

Two access paths, tried in order (per the architecture review):
1. The official partner API (api.fantasypros.com) when FANTASYPROS_API_KEY
   is configured — clean JSON, season-parameterized, sanctioned.
2. The public rankings pages, which embed the full consensus payload as an
   `ecrData` JSON blob in the page source — fetched with plain HTTP and
   browser-like headers. NOTE: the pages always show the CURRENT season's
   consensus; the season argument only applies on the API path.

This is the source flagged with real anti-bot risk (Cloudflare). If the
page fetch starts failing, the fix is a browser-backed transport
(PlaywrightTransport seam), not header tweaking — the adapter's parsing
is transport-agnostic on purpose. Failures surface loudly as failed
batches in /rankings/refresh and /rankings/status.
"""
import json
import re
from typing import List, Optional

from models.config import FANTASYPROS_API_KEY

from .base import BaseSourceAdapter, SourceFetchError, SourceRecord

API_URL = (
    "https://api.fantasypros.com/public/v2/json/nfl/{season}/consensus-rankings"
)
API_SCORING = {"standard": "STD", "half_ppr": "HALF", "ppr": "PPR"}

PAGE_URLS = {
    "standard": "https://www.fantasypros.com/nfl/rankings/consensus-cheatsheets.php",
    "half_ppr": "https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php",
    "ppr": "https://www.fantasypros.com/nfl/rankings/ppr-cheatsheets.php",
}

# The page path presents as an ordinary browser visit
PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

POSITIONS = {"QB", "RB", "WR", "TE", "K", "DST"}

_POS_RANK_NUMBER = re.compile(r"(\d+)$")


def _position_rank_number(pos_rank) -> Optional[float]:
    """'WR12' -> 12.0"""
    if not pos_rank:
        return None
    match = _POS_RANK_NUMBER.search(str(pos_rank))
    return float(match.group(1)) if match else None


def extract_ecr_data(html: str) -> dict:
    """Pull the balanced ecrData JSON object out of the page source"""
    marker = re.search(r"ecrData\s*=\s*", html)
    if not marker:
        raise SourceFetchError(
            "fantasypros: no ecrData found in page (markup changed or blocked)"
        )
    try:
        data, _ = json.JSONDecoder().raw_decode(html[marker.end():])
    except ValueError as exc:
        raise SourceFetchError(f"fantasypros: could not parse ecrData: {exc}")
    return data


class FantasyProsAdapter(BaseSourceAdapter):
    source_name = "fantasypros"
    min_request_interval_seconds = 5.0  # be a polite guest on the risky source

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key if api_key is not None else FANTASYPROS_API_KEY

    async def fetch(self, season: int, scoring_format: str) -> List[SourceRecord]:
        if scoring_format not in API_SCORING:
            raise SourceFetchError(
                f"fantasypros: unsupported scoring format '{scoring_format}'"
            )
        if self.api_key:
            try:
                return await self._fetch_api(season, scoring_format)
            except SourceFetchError as exc:
                print(f"WARNING: fantasypros API failed ({exc}); trying page embed")
        return await self._fetch_page(scoring_format)

    async def _fetch_api(self, season, scoring_format) -> List[SourceRecord]:
        response = await self._get(
            API_URL.format(season=season),
            params={
                "type": "draft",
                "scoring": API_SCORING[scoring_format],
                "position": "ALL",
                "week": 0,
            },
            headers={"x-api-key": self.api_key},
        )
        return self._parse_players(response.json(), mode="api")

    async def _fetch_page(self, scoring_format) -> List[SourceRecord]:
        response = await self._get(
            PAGE_URLS[scoring_format], headers=PAGE_HEADERS
        )
        return self._parse_players(extract_ecr_data(response.text), mode="page")

    def _parse_players(self, payload: dict, mode: str) -> List[SourceRecord]:
        records = []
        for row in payload.get("players", []):
            position = row.get("player_position_id")
            if position not in POSITIONS or not row.get("player_name"):
                continue
            records.append(
                SourceRecord(
                    raw_name=row["player_name"],  # defenses: "Dallas Cowboys"
                    position=position,
                    nfl_team=row.get("player_team_id"),
                    rank=row.get("rank_ecr"),
                    position_rank=_position_rank_number(row.get("pos_rank")),
                    tier=row.get("tier"),
                    extra={"access_mode": mode},
                )
            )
        if not records:
            raise SourceFetchError(
                f"fantasypros ({mode}): response contained no usable players"
            )
        return records
