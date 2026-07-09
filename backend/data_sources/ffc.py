# -*- coding: utf-8 -*-
"""
FANTASY FOOTBALL CALCULATOR ADAPTER (crowd-sourced ADP)

Official, documented, free REST API; attribution requested by FFC. ADP
only — no projections. Crucially the year parameter serves PAST seasons
too, which makes this the project's source of historical ADP for the
owner-tendency reach features (Phase 3 backfill).

API docs: https://help.fantasyfootballcalculator.com/article/42-adp-rest-api
"""
from typing import List

from .base import BaseSourceAdapter, SourceFetchError, SourceRecord

ADP_URL = "https://fantasyfootballcalculator.com/api/v1/adp/{format}"

FORMAT_SLUGS = {"standard": "standard", "half_ppr": "half-ppr", "ppr": "ppr"}


class FantasyFootballCalculatorAdapter(BaseSourceAdapter):
    source_name = "ffc"
    min_request_interval_seconds = 2.0

    def __init__(self, teams: int = 12, **kwargs):
        super().__init__(**kwargs)
        self.teams = teams  # league size the mock-draft ADP is drawn from

    async def fetch(self, season: int, scoring_format: str) -> List[SourceRecord]:
        slug = FORMAT_SLUGS.get(scoring_format)
        if slug is None:
            raise SourceFetchError(
                f"ffc: unsupported scoring format '{scoring_format}'"
            )
        response = await self._get(
            ADP_URL.format(format=slug),
            params={"teams": self.teams, "year": season, "position": "all"},
        )
        payload = response.json()
        if payload.get("status") != "Success":
            raise SourceFetchError(f"ffc: API status was '{payload.get('status')}'")
        records = []
        for row in payload.get("players", []):
            adp = row.get("adp") or None
            if not row.get("name") or adp is None:
                continue
            records.append(
                SourceRecord(
                    raw_name=row["name"],  # defenses arrive as "<City> Defense"
                    position=row.get("position", ""),  # FFC says PK for kickers
                    nfl_team=row.get("team"),
                    adp=adp,
                    extra={
                        "times_drafted": row.get("times_drafted"),
                        "stdev": row.get("stdev"),
                    },
                )
            )
        if not records:
            raise SourceFetchError("ffc: response contained no usable players")
        return records
