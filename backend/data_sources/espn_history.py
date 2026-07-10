# -*- coding: utf-8 -*-
"""
ESPN HISTORICAL DRAFT INGESTION (owner tendency raw material)

Pulls pick-by-pick draft history for the user's ESPN leagues through the
espn-api library (Addendum A: adopted over a custom client), normalizes
it into the historical_picks collection, and backfills each pick's
historical ADP + position from FantasyFootballCalculator's
year-parameterized API — the piece that makes reach-vs-ADP features
possible for past seasons.

Owner tendency profiling is ESPN-exclusive; this module and FFC's
historical ADP are its only inputs. espn-api objects never leave this
module: picks are normalized to HistoricalPick rows at the boundary and
the library types are discarded (the League document / Monte Carlo hot
loop never sees them).

Per-season outcomes (including failures and auction detection) are
persisted to historical_ingest_log, because 20+ years of ESPN history
WILL have gaps and they must be visible, not assumed away.
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from starlette.concurrency import run_in_threadpool

from models.config import (
    DATA_SOURCE_CACHE_DIR,
    DATA_SOURCE_CACHE_TTL_SECONDS,
    DRAFT_YEAR,
    ESPN_S2,
    ESPN_SWID,
)
from models.sources import HistoricalIngestLog, HistoricalPick

from .base import SourceFetchError
from .cache import RawResponseCache
from .ffc import FantasyFootballCalculatorAdapter
from .resolver import dst_alias_to_abbrev, normalize_name, normalize_position

# FFC publishes ADP for these league sizes only
FFC_TEAM_SIZES = (8, 10, 12, 14)
# FFC's standard-scoring series reaches furthest back; reach features
# measure pick-vs-market distance, where the format nuance is second-order
HISTORICAL_ADP_FORMAT = "standard"


def create_espn_league(espn_league_id: int, season: int, espn_s2=None, swid=None):
    """The real espn-api constructor; tests substitute a stub factory"""
    from espn_api.football import League

    return League(
        league_id=espn_league_id, year=season, espn_s2=espn_s2, swid=swid
    )


def _owner_of(team) -> Tuple[Optional[str], Optional[str]]:
    """(member_guid, display_name) from an espn-api Team, any vintage"""
    owners = getattr(team, "owners", None) or []
    first = owners[0] if owners else None
    if isinstance(first, dict):
        name = " ".join(
            part
            for part in [first.get("firstName"), first.get("lastName")]
            if part
        ) or first.get("displayName")
        return first.get("id"), name or None
    if first:  # very old espn-api versions stored plain strings
        return str(first), None
    return None, None


def _closest_team_size(count: int) -> int:
    return min(FFC_TEAM_SIZES, key=lambda size: abs(size - count))


class EspnHistoryIngester:
    """Fetches one league-season at a time; injectable for tests"""

    def __init__(
        self,
        espn_s2: Optional[str] = None,
        swid: Optional[str] = None,
        league_factory=None,
        ffc_adapter: Optional[FantasyFootballCalculatorAdapter] = None,
    ):
        self.espn_s2 = espn_s2 if espn_s2 is not None else ESPN_S2
        self.swid = swid if swid is not None else ESPN_SWID
        self.league_factory = league_factory or create_espn_league
        self._ffc_adapter = ffc_adapter
        self._adp_maps: Dict[tuple, dict] = {}

    def _league(self, espn_league_id: int, season: int):
        return self.league_factory(
            espn_league_id, season, espn_s2=self.espn_s2, swid=self.swid
        )

    # -- ESPN side (sync; espn-api uses requests) --------------------------

    def discover_seasons(self, espn_league_id: int, current_season: int) -> List[int]:
        """current season + everything ESPN reports as previous seasons"""
        league = self._league(espn_league_id, current_season)
        previous = [
            int(season)
            for season in getattr(league, "previousSeasons", []) or []
        ]
        return sorted(set(previous + [current_season]))

    def fetch_season(self, espn_league_id: int, season: int) -> dict:
        """Raw pick rows for one league-season (positions/ADP added later)"""
        league = self._league(espn_league_id, season)
        teams = list(getattr(league, "teams", []) or [])
        team_count = len(teams) or 1
        picks = []
        for pick in getattr(league, "draft", []) or []:
            team = pick.team
            member_guid, display_name = _owner_of(team)
            bid = getattr(pick, "bid_amount", 0) or 0
            picks.append(
                {
                    "espn_league_id": espn_league_id,
                    "season": season,
                    "overall_pick": (pick.round_num - 1) * team_count
                    + pick.round_pick,
                    "round_num": pick.round_num,
                    "round_pick": pick.round_pick,
                    "member_guid": member_guid,
                    "owner_display_name": display_name,
                    "espn_team_id": getattr(team, "team_id", 0) or 0,
                    "raw_player_name": pick.playerName,
                    "is_keeper": bool(getattr(pick, "keeper_status", False)),
                    "bid_amount": int(bid) if bid else None,
                }
            )
        if not picks:
            raise SourceFetchError(
                f"espn league {espn_league_id} season {season}: no draft picks"
            )
        return {"picks": picks, "team_count": team_count}

    # -- FFC side (async): historical ADP + position backfill --------------

    def _ffc(self) -> FantasyFootballCalculatorAdapter:
        if self._ffc_adapter is None:
            self._ffc_adapter = FantasyFootballCalculatorAdapter(
                cache=RawResponseCache(
                    DATA_SOURCE_CACHE_DIR, DATA_SOURCE_CACHE_TTL_SECONDS
                )
            )
        return self._ffc_adapter

    async def adp_map(self, season: int, team_count: int) -> dict:
        """
        {normalized player name -> (position, adp)} plus
        {("dst", team_abbrev) -> (position, adp)} for defenses, or {} when
        FFC has nothing for that season (features degrade gracefully)
        """
        key = (season, _closest_team_size(team_count))
        if key in self._adp_maps:
            return self._adp_maps[key]
        adapter = self._ffc()
        adapter.teams = key[1]
        mapping = {}
        try:
            records = await adapter.fetch(season, HISTORICAL_ADP_FORMAT)
        except SourceFetchError as exc:
            print(f"WARNING: no historical ADP for {season} ({exc})")
            records = []
        for record in records:
            position = normalize_position(record.position)
            entry = (position, record.adp)
            if position == "dst":
                abbrev = record.nfl_team or dst_alias_to_abbrev(record.raw_name)
                if abbrev:
                    mapping.setdefault(("dst", abbrev), entry)
                continue
            mapping.setdefault(normalize_name(record.raw_name), entry)
        self._adp_maps[key] = mapping
        return mapping

    @staticmethod
    def enrich_picks(picks: List[dict], adp_map: dict):
        """Attach position + historical ADP where the market knew the player"""
        for pick in picks:
            name = pick["raw_player_name"]
            entry = adp_map.get(normalize_name(name))
            if entry is None:
                abbrev = dst_alias_to_abbrev(name)
                if abbrev:
                    entry = adp_map.get(("dst", abbrev))
            if entry is not None:
                pick["position"], pick["historical_adp"] = entry


async def ingest_league_history(
    engine,
    espn_league_id: int,
    seasons: Optional[List[int]] = None,
    ingester: Optional[EspnHistoryIngester] = None,
    current_season: int = DRAFT_YEAR,
) -> dict:
    """
    Ingest every (requested or discoverable) season of one league:
    replace that league-season's historical_picks, log the outcome per
    season, and return a summary
    """
    ingester = ingester or EspnHistoryIngester()
    if not seasons:
        try:
            seasons = await run_in_threadpool(
                ingester.discover_seasons, espn_league_id, current_season
            )
        except Exception as exc:
            raise SourceFetchError(
                f"Could not reach ESPN league {espn_league_id} to discover "
                f"seasons ({type(exc).__name__}: {exc}); check the league id "
                "and ESPN_S2/ESPN_SWID cookies, or pass seasons explicitly"
            )

    picks_collection = engine.get_collection(HistoricalPick)
    results = {}
    for season in sorted(seasons):
        log = HistoricalIngestLog(
            espn_league_id=espn_league_id, season=season, fetched_at=datetime.now()
        )
        try:
            fetched = await run_in_threadpool(
                ingester.fetch_season, espn_league_id, season
            )
        except Exception as exc:
            log.success = False
            log.error = f"{type(exc).__name__}: {exc}"
            await engine.save(log)
            results[season] = {"success": False, "error": log.error}
            continue

        picks = fetched["picks"]
        adp_map = await ingester.adp_map(season, fetched["team_count"])
        ingester.enrich_picks(picks, adp_map)

        # Idempotent re-ingest: this league-season is replaced wholesale
        await picks_collection.delete_many(
            {"espn_league_id": espn_league_id, "season": season}
        )
        await engine.save_all([HistoricalPick(**pick) for pick in picks])

        log.picks = len(picks)
        log.keepers = sum(1 for pick in picks if pick["is_keeper"])
        log.auction = any(pick["bid_amount"] for pick in picks)
        log.position_matched = sum(1 for pick in picks if pick.get("position"))
        log.adp_matched = sum(
            1 for pick in picks if pick.get("historical_adp") is not None
        )
        await engine.save(log)
        results[season] = {
            "success": True,
            "picks": log.picks,
            "keepers": log.keepers,
            "auction": log.auction,
            "position_matched": log.position_matched,
            "adp_matched": log.adp_matched,
        }

    return {
        "espn_league_id": espn_league_id,
        "seasons": results,
        "ingested_seasons": sum(1 for r in results.values() if r["success"]),
        "failed_seasons": sum(1 for r in results.values() if not r["success"]),
    }
