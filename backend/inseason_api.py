# -*- coding: utf-8 -*-
"""
CACHED-ONLY IN-SEASON READ PATH (PHASE B, TASK B4)

Every read the multi-league view and team-perspective switcher makes
goes through this router, and this router can only see Mongo.

HARD CONSTRAINT, ENFORCED HERE AND NOT IN THE UI: switching league or
perspective NEVER triggers a scrape, an ESPN call, or any other
external fetch. The enforcement is structural, not conventional —

- this module imports nothing from data_sources (no transport, no
  adapters); test_inseason_api.py fails the build if that changes, and
  also drives every GET with the HTTP transport rigged to explode;
- refresh exists only as an explicit POST /inseason/sync in app.py —
  no GET anywhere can fetch;
- every response carries a `freshness` envelope + human `warnings`
  from league_freshness(), so serving cached data degrades VISIBLY
  (stale age, expired cookies) instead of quietly looking fresh.

Future features (C-F) that want in-season reads add endpoints HERE, and
inherit the constraint; anything that needs an external call belongs on
an explicit POST in app.py instead.

Engine binding: app.py calls configure() with a late-bound getter so
tests that swap app.engine (conftest) are honored automatically.
"""
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException
from odmantic import query

from models.config import DRAFT_YEAR
from models.matchup_strength import defense_position_strength
from models.inseason import (
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueTransaction,
    ProGame,
    TeamWeekRoster,
    WeeklyMatchup,
    league_freshness,
    week_lock_times,
)

router = APIRouter(prefix="/inseason", tags=["inseason"])

_engine_getter: Optional[Callable] = None


def configure(engine_getter: Callable):
    global _engine_getter
    _engine_getter = engine_getter


def _engine():
    if _engine_getter is None:
        raise RuntimeError("inseason_api.configure() was never called")
    return _engine_getter()


async def _league_or_404(engine, espn_league_id: int, season: int) -> InSeasonLeague:
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"ESPN league {espn_league_id} has no synced data for "
                f"{season}; run POST /inseason/sync first"
            ),
        )
    return league


async def _envelope(engine, espn_league_id: int, season: int, data) -> dict:
    freshness = await league_freshness(engine, espn_league_id, season)
    return {
        "data": data,
        "freshness": freshness,
        "warnings": freshness["warnings"],
    }


@router.get("/overview")
async def get_overview(season: int = DRAFT_YEAR):
    """
    Every synced league with its teams and freshness — the data source
    for the league selector and team-perspective dropdown. One call,
    zero external fetches, works fully offline from cache.
    """
    engine = _engine()
    leagues = await engine.find(
        InSeasonLeague,
        InSeasonLeague.season == season,
        sort=query.asc(InSeasonLeague.espn_league_id),
    )
    entries = []
    for league in leagues:
        freshness = await league_freshness(engine, league.espn_league_id, season)
        entries.append(
            {
                "league": league.model_dump(exclude={"id"}),
                "freshness": freshness,
                "warnings": freshness["warnings"],
            }
        )
    return {"season": season, "leagues": entries}


@router.get("/league/{espn_league_id}/roster")
async def get_roster(
    espn_league_id: int,
    espn_team_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """One team's roster for one week, from any team's perspective"""
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    if not any(team.espn_team_id == espn_team_id for team in league.teams):
        raise HTTPException(
            status_code=404,
            detail=f"No team {espn_team_id} in league {espn_league_id}",
        )
    roster = await engine.find_one(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == espn_league_id)
        & (TeamWeekRoster.season == season)
        & (TeamWeekRoster.week == week)
        & (TeamWeekRoster.espn_team_id == espn_team_id),
    )
    return await _envelope(
        engine,
        espn_league_id,
        season,
        roster.model_dump(exclude={"id"}) if roster else None,
    )


@router.get("/league/{espn_league_id}/matchups")
async def get_matchups(
    espn_league_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """Matchups with scores; defaults to the current matchup period"""
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.current_matchup_period
    matchups = await engine.find(
        WeeklyMatchup,
        (WeeklyMatchup.espn_league_id == espn_league_id)
        & (WeeklyMatchup.season == season)
        & (WeeklyMatchup.week == week),
        sort=query.asc(WeeklyMatchup.home_team_id),
    )
    return await _envelope(
        engine,
        espn_league_id,
        season,
        {
            "week": week,
            "matchups": [matchup.model_dump(exclude={"id"}) for matchup in matchups],
        },
    )


@router.get("/league/{espn_league_id}/transactions")
async def get_transactions(
    espn_league_id: int,
    week: Optional[int] = None,
    limit: int = 50,
    season: int = DRAFT_YEAR,
):
    """Recent transactions, newest first; optionally one week only"""
    engine = _engine()
    await _league_or_404(engine, espn_league_id, season)
    criteria = (LeagueTransaction.espn_league_id == espn_league_id) & (
        LeagueTransaction.season == season
    )
    if week is not None:
        criteria = criteria & (LeagueTransaction.week == week)
    transactions = await engine.find(
        LeagueTransaction,
        criteria,
        sort=(query.desc(LeagueTransaction.processed_at)),
        limit=limit,
    )
    return await _envelope(
        engine,
        espn_league_id,
        season,
        [transaction.model_dump(exclude={"id"}) for transaction in transactions],
    )


@router.get("/league/{espn_league_id}/free_agents")
async def get_free_agents(
    espn_league_id: int,
    position: Optional[str] = None,
    limit: int = 50,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """The latest synced free-agent pool, position-filterable (C3 reads this)"""
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    snapshot = await engine.find_one(
        FreeAgentSnapshot,
        (FreeAgentSnapshot.espn_league_id == espn_league_id)
        & (FreeAgentSnapshot.season == season)
        & (FreeAgentSnapshot.week == week),
        sort=(query.desc(FreeAgentSnapshot.synced_at), query.desc(FreeAgentSnapshot.id)),
    )
    entries: List[dict] = []
    if snapshot:
        for entry in snapshot.entries:
            if position and (entry.position or "").upper() != position.upper():
                continue
            entries.append(entry.model_dump())
            if len(entries) >= limit:
                break
    return await _envelope(
        engine,
        espn_league_id,
        season,
        {"week": week, "free_agents": entries},
    )


@router.get("/matchup_strength")
async def get_matchup_strength(
    position: Optional[str] = None,
    through_week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    Opponent-vs-position strength across all synced leagues (C2).
    League-independent by construction — ratios are normalized inside
    each league before aggregation — and computed entirely from Mongo,
    so it inherits the cached-only constraint. Week 1 returns neutral
    multipliers with confidence "none"; see models/matchup_strength.py
    for the methodology contract.
    """
    engine = _engine()
    strength = await defense_position_strength(
        engine, season, through_week=through_week
    )
    if position is not None:
        wanted = position.upper()
        if wanted not in strength["positions"]:
            raise HTTPException(
                status_code=404, detail=f"Unknown position {position}"
            )
        strength["positions"] = {wanted: strength["positions"][wanted]}
    return strength


@router.get("/league/{espn_league_id}/locks")
async def get_locks(
    espn_league_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    Lineup-lock times for one week: first lock (Wednesday-opener aware,
    it is simply the earliest kickoff), final lock, and per-NFL-team
    locks — what C6's early-lock strategy and the reminder Routines key on
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    games = await engine.find(
        ProGame, (ProGame.season == season) & (ProGame.week == week)
    )
    locks = week_lock_times(games)
    data = {"week": week, "locks": None}
    if locks:
        data["locks"] = {
            "first_lock": locks["first_lock"].isoformat(),
            "final_lock": locks["final_lock"].isoformat(),
            "first_game": locks["first_game"],
            "team_locks": {
                team: kickoff.isoformat()
                for team, kickoff in sorted(locks["team_locks"].items())
            },
        }
    return await _envelope(engine, espn_league_id, season, data)
