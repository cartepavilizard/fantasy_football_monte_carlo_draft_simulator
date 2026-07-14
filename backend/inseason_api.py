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
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from odmantic import query
from pydantic import BaseModel

from models.config import DRAFT_YEAR
from models.handcuffs import (
    available_handcuff_flags,
    delete_handcuff,
    list_handcuffs,
    seed_handcuffs,
    upsert_handcuff,
)
from models.lineup import optimize_lineup
from models.matchup_strength import defense_position_strength
from models.playoff_sos import playoff_schedule_strength, playoff_sos_for_league
from models.streaming import streaming_recommendations
from models.trade_valuation import (
    build_context,
    evaluate_trade,
    player_value,
    validate_trade,
)
from models.usage_shifts import detect_usage_shifts
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


@router.get("/league/{espn_league_id}/lineup")
async def get_lineup(
    espn_league_id: int,
    espn_team_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    The full lineup call (C1): optimal legal lineup for one team-week
    from ESPN weekly projections with C2's matchup tilt, the moves to
    get there, per-player matchup context, and C6 lock guidance. Serves
    entirely from Mongo — freshness comes from the sync paths, and the
    envelope says how old the data is.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    if not any(team.espn_team_id == espn_team_id for team in league.teams):
        raise HTTPException(
            status_code=404,
            detail=f"No team {espn_team_id} in league {espn_league_id}",
        )
    data = await optimize_lineup(engine, league, espn_team_id, week)
    return await _envelope(engine, espn_league_id, season, data)


@router.get("/league/{espn_league_id}/streaming")
async def get_streaming(
    espn_league_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    K/DST streaming ranks (C3): the latest free-agent K/DST pool ranked
    by C2's matchup-adjusted points, tie-broken by the raw multiplier,
    with each row's matchup context and C9's homer check attached.
    Mongo-only, inherits B4's cached-only constraint.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    data = await streaming_recommendations(engine, espn_league_id, season, week)
    return await _envelope(engine, espn_league_id, season, data)


# --- trade valuation (E1): market value + roster fit, Mongo-only ------------


class TradeProposal(BaseModel):
    """POST body for trade evaluation. POST because it carries a proposal,
    NOT because it fetches — the handler is pure Mongo reads and is covered
    by the rigged-transport enforcement test."""

    team_a: int
    team_b: int
    sends_a: List[int] = []
    sends_b: List[int] = []
    season: Optional[int] = None
    week: Optional[int] = None
    availability_overrides: Optional[Dict[int, Dict[int, float]]] = None


@router.post("/league/{espn_league_id}/trade/evaluate")
async def post_trade_evaluate(espn_league_id: int, proposal: TradeProposal):
    """
    Grade a trade proposal (E1) on both value lenses: player_value (market
    fairness) and fit_delta (does it help each roster). Serves entirely
    from Mongo — inherits B4's cached-only constraint despite being a POST;
    the body is a proposal, not a fetch trigger.

    Live-week note: a mid-week trade slightly overcounts the live week for
    both sides symmetrically — values use full-week expected points and do
    not discount an already-locked Thursday player.
    """
    engine = _engine()
    season = proposal.season or DRAFT_YEAR
    league = await _league_or_404(engine, espn_league_id, season)
    ctx = await build_context(engine, league, week=proposal.week)
    errors = validate_trade(
        ctx, proposal.team_a, proposal.team_b, proposal.sends_a, proposal.sends_b
    )
    if errors:
        raise HTTPException(status_code=422, detail=errors[0])
    data = evaluate_trade(
        ctx,
        proposal.team_a,
        proposal.team_b,
        proposal.sends_a,
        proposal.sends_b,
        overrides=proposal.availability_overrides,
    )
    return await _envelope(engine, espn_league_id, season, data)


@router.get("/league/{espn_league_id}/player_values")
async def get_player_values(
    espn_league_id: int,
    espn_team_id: Optional[int] = None,
    position: Optional[str] = None,
    limit: int = 25,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    player_value (E1) for one team's roster, and — when a position is
    given — the top `limit` free agents at that position too. The UI's
    value browser and a cheap sanity surface for tuning; Mongo-only,
    inherits B4's cached-only constraint.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    if espn_team_id is None and position is None:
        raise HTTPException(
            status_code=422,
            detail="provide espn_team_id and/or position to value players",
        )
    ctx = await build_context(engine, league, week=week)
    values = []
    if espn_team_id is not None:
        values.extend(
            player_value(ctx, pid) for pid in ctx.rosters.get(espn_team_id, [])
        )
    if position is not None:
        wanted = position.upper()
        free_agents = [
            player_value(ctx, pid)
            for pid, meta in ctx.players.items()
            if meta.get("espn_team_id") is None
            and (meta.get("position") or "").upper() == wanted
        ]
        free_agents.sort(key=lambda entry: entry["value"], reverse=True)
        values.extend(free_agents[:limit])
    values.sort(key=lambda entry: entry["value"], reverse=True)
    return await _envelope(
        engine,
        espn_league_id,
        season,
        {"week": ctx.w0, "weeks_remaining": len(ctx.horizon), "values": values},
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


@router.get("/playoff_sos")
async def get_playoff_sos(
    position: Optional[str] = None,
    espn_league_id: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    Weeks-14-16 playoff strength of schedule (C5): for each NFL team, per
    position, C2's defense_position_strength() multipliers summed across
    its playoff-window opponents and ranked (rank 1 = easiest schedule).
    Computed entirely from Mongo, so it inherits B4's cached-only
    constraint. Confidence and the early-season "all neutral" `note`
    carry through from C2 unchanged — a September call is honest about
    being noise, not a confident-looking ranking. Optionally scoped to
    one league via espn_league_id: adds `rosters`, each fantasy team's
    current starters joined against the same table — how friendly your
    playoff schedule actually is, not just the league's.
    """
    engine = _engine()
    sos = await playoff_schedule_strength(engine, season)
    if espn_league_id is not None:
        league = await _league_or_404(engine, espn_league_id, season)
        sos["rosters"] = await playoff_sos_for_league(engine, league, season, sos)
    if position is not None:
        wanted = position.upper()
        if wanted not in sos["positions"]:
            raise HTTPException(
                status_code=404, detail=f"Unknown position {position}"
            )
        sos["positions"] = {wanted: sos["positions"][wanted]}
    return sos


# --- handcuff map (C7): curated, Mongo-only, no external calls ---------------


@router.get("/handcuffs")
async def get_handcuffs():
    """The starter -> direct-backup map (C7), sorted by starter"""
    pairs = await list_handcuffs(_engine())
    return {"handcuffs": [pair.model_dump(exclude={"id"}) for pair in pairs]}


@router.post("/handcuffs")
async def set_handcuff(
    starter_name: str,
    handcuff_name: str,
    nfl_team: Optional[str] = None,
    note: Optional[str] = None,
):
    """Create or repoint one mapping (marked manual; survives re-seeds)"""
    pair = await upsert_handcuff(
        _engine(), starter_name, handcuff_name, nfl_team=nfl_team, note=note
    )
    return pair.model_dump(exclude={"id"})


@router.post("/handcuffs/seed")
async def seed_handcuff_table():
    """Insert missing seed pairs; never touches existing/manual rows"""
    return await seed_handcuffs(_engine())


@router.delete("/handcuffs/{starter_name}")
async def remove_handcuff(starter_name: str):
    """Delete one mapping (e.g. a backfield that became a committee)"""
    if not await delete_handcuff(_engine(), starter_name):
        raise HTTPException(
            status_code=404, detail=f"No handcuff mapping for {starter_name}"
        )
    return {"deleted": starter_name}


@router.get("/league/{espn_league_id}/handcuffs")
async def get_league_handcuffs(
    espn_league_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    Handcuff flags for one league-week (C7): the curated map joined
    against this league's rostered starters and free-agent pool, with
    priority and C9's homer check attached. Mongo-only, inherits B4's
    cached-only constraint.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    flags = await available_handcuff_flags(engine, espn_league_id, season, week)
    return await _envelope(engine, espn_league_id, season, {"week": week, "handcuffs": flags})


@router.get("/usage_shifts")
async def get_usage_shifts(week: int, season: int = DRAFT_YEAR):
    """
    Every meaningful usage shift for one NFL week (C4) — snap-share and
    target-share moves vs each player's trailing baseline, straight
    from the ingested PlayerWeekUsage rows in Mongo. League-independent;
    the notification path additionally filters to rostered/free-agent
    players, but this read returns them all for the trends view.
    """
    engine = _engine()
    return {
        "season": season,
        "week": week,
        "shifts": await detect_usage_shifts(engine, season, week),
    }


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
