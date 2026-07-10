# -*- coding: utf-8 -*-
"""
MONTE CARLO FANTASY FOOTBALL DRAFT SIMULATOR BACKEND
"""
import asyncio
from concurrent.futures import ProcessPoolExecutor
import csv
from datetime import datetime
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from motor.motor_asyncio import AsyncIOMotorClient
from odmantic import AIOEngine, ObjectId, query
import random
from sklearn.base import RegressorMixin
from sklearn.linear_model import LogisticRegression
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
import time
from typing import List

from data_sources import service as ranking_service
from data_sources.base import SourceFetchError
from data_sources.espn_history import ingest_league_history
import profiling
from models.config import (
    DRAFT_YEAR,
    LOCAL,
    ROSTER_SIZE,
    ROUND_SIZE,
    SCORING_FORMAT,
    SNAKE_DRAFT,
)
from models.player import Player, Players, PlayerPoints
from models.position import PositionMaxPoints, PositionSizes, PositionTierDistributions
from models.sources import BlendedRanking, HistoricalPick, OwnerAlias, OwnerProfile
from models.team import (
    Draft,
    DraftSimple,
    League,
    LogisticRegressionVariables,
    LeagueSimple,
    MonteCarloSimulationResult,
    Team,
)


# Metadata
tags_metadata = [
    {
        "name": "league",
        "description": "Leagues are the centralized setting and must be initialized with a list of teams.",
    },
    {
        "name": "player",
        "description": "Draftable players in a league, with projections of their performance this season.",
    },
    {
        "name": "historical_player",
        "description": "Historical players in a league, which determine position tier distributions.",
    },
    {
        "name": "historical_draft",
        "description": "Historical drafts in a league, which train the logistic regression model.",
    },
    {
        "name": "draft",
        "description": "Drafts are copies of leagues, which can simulate a round-by-round draft.",
    },
    {
        "name": "rankings",
        "description": "Automated ranking aggregation: fetch external sources, blend them, and sync the blend into a league's players.",
    },
    {
        "name": "owners",
        "description": "Owner tendency profiling: ingest ESPN historical drafts per owner and build frequency/average tendency profiles.",
    },
]


# Initialize app and engine
if LOCAL:
    print("Running locally")
    client = AsyncIOMotorClient("mongodb://localhost:27017")
else:
    print("Running in Docker")
    client = AsyncIOMotorClient("mongodb://mongodb:27017")
app = FastAPI(
    title="FF Monte Carlo Draft Simulator", version="0.0.1", openapi_tags=tags_metadata
)
engine = AIOEngine(
    database="fantasy-football",
    client=client,
)

# Monte Carlo simulations are CPU-bound; run them in a separate process so
# they don't hold the GIL and starve other requests for their ~30s duration
process_pool = ProcessPoolExecutor(max_workers=2)


# Include origins for CORS
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://127.0.0.1",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Helper functions
async def get_a_league_by_id(league_id: ObjectId) -> League:
    """
    Get a league by its ID
    """
    league = await engine.find_one(League, League.id == league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    return league


async def get_a_draft_by_id(draft_id: ObjectId) -> Draft:
    """
    Get a draft by its ID
    """
    draft = await engine.find_one(Draft, Draft.id == draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


def read_csv_upload(content: bytes, required_columns: set) -> list:
    """
    Parse an uploaded CSV and 422 with a clear message on bad shape
    """
    rows = list(csv.DictReader(content.decode("utf-8-sig").splitlines()))
    if not rows:
        raise HTTPException(status_code=422, detail="CSV file is empty")
    missing = required_columns - set(rows[0].keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"CSV missing required columns: {sorted(missing)}",
        )
    return rows


def create_max_points(
    players: Players, draft_year: str = str(DRAFT_YEAR)
) -> PositionMaxPoints:
    """
    Use the top player in each position to set max points
    (so that any outliers are not too extreme)
    """
    max_points = {}
    for position in ["qb", "rb", "wr", "te", "dst", "k"]:
        max_points[position] = max(
            (
                player.points[draft_year].projected_points
                for player in players.__getattribute__(position)
            ),
            default=0.0,  # a source blend may legitimately lack a position
        )
    return PositionMaxPoints(**max_points)


def create_historical_distributions(
    players: Players, draft_year: str = str(DRAFT_YEAR)
) -> PositionTierDistributions:
    """
    Use the difference between historical performance and projections
    to create distributions for each position tier
    (replicating injuries, breakouts, and busts from the past)
    """
    distributions = {}
    for player in players.players:

        # Append or create the list for the position tier
        if player.position_tier not in distributions:
            distributions[player.position_tier] = []

        # For each year available in the player's points, get the percentage adjustment
        for year, points in player.points.items():
            if (
                points.actual_points is not None
                and points.projected_points > 0
                and int(year) < int(draft_year)
            ):  # Only use historical data; keep 0-point (injury) seasons
                distributions[player.position_tier].append(
                    (points.actual_points - points.projected_points)
                    / points.projected_points
                )

    # Return the position tier distributions
    return PositionTierDistributions(**distributions)


def fit_logistic_regression_model(
    logistic_regression_variables: LogisticRegressionVariables,
) -> RegressorMixin:
    """
    Train the model for simulating opponent draft picks
    """
    try:
        draft_pick_model = LogisticRegression(max_iter=1000)
        x = [[int(x)] for x in logistic_regression_variables.x]
        y = logistic_regression_variables.y
        draft_pick_model.fit(x, y)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to train logistic regression model: {exc}",
        )
    return draft_pick_model


def simulate_pick(
    league: League,
    draft_pick_model: RegressorMixin,
) -> str:
    """
    Simulate a pick using the logistic model to get probabilities for each position
    """
    players = league.players

    # Calculate the weights
    team_index = league.draft_order[0]
    team = league.teams[team_index]
    weights = team.draft_turn_position_weights(
        league.current_draft_turn + 1, draft_pick_model
    )
    weights = {k.lower(): v for k, v in weights.items()}

    # Randomly choose which position to pick, based on the weights
    positions = list(weights.keys())
    weights = list(weights.values())
    position_players = []
    while len(position_players) == 0:
        # If the total weights are zero, just go random
        # (this can happen at the end of the draft)
        if sum(weights) == 0:
            weights = [1 for _ in positions]
        selection = random.choices(positions, weights=weights)[0]
        position_players = [
            x for x in getattr(players, selection) if x.drafted == False
        ]

        # If there are no players left in that position, remove it from the list
        if len(position_players) == 0:
            index = positions.index(selection)
            positions.pop(index)
            weights.pop(index)

    # Draft the best draftable player within that position
    player = position_players[0]
    return player.name


def draft_player(player_name: str, league: League):
    """
    Draft a player by name and update the league and players
    """
    players = league.players
    player = [player for player in players.players if player.name == player_name]
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    else:
        player = player[0]
    if player.drafted:
        raise HTTPException(status_code=400, detail="Player already drafted")

    # Set the player as drafted within the league
    position = player.position.lower()
    for k in ["players", position]:
        if hasattr(players, k):
            position_players = getattr(players, k)
            # Find the player in the list by name
            player_index = next(
                (
                    index
                    for index, player in enumerate(position_players)
                    if player.name == player_name
                ),
                None,
            )
            new_player = Player(**position_players[player_index].model_dump())
            new_player.drafted = True
            position_players[player_index] = new_player

    # Draft the player
    league.add_player_to_current_draft_turn_team(player)
    return


def simulate_draft(league: League, draft_pick_model: RegressorMixin):
    """
    Simulate an entire draft using the logistic model
    """
    draft_order = league.draft_order.copy()
    for _ in enumerate(draft_order):
        player_name = simulate_pick(league, draft_pick_model)
        draft_player(player_name, league)
    return


def monte_carlo_draft(
    league: League,
    seconds: float = 30,  # Set to whatever time is best for the draft
) -> MonteCarloSimulationResult:
    """
    Simulate drafts for each position and return the average points scored
    to determine which position is best to draft
    """
    simulator_team = [i for i, team in enumerate(league.teams) if team.simulator]
    if not simulator_team:
        raise HTTPException(
            status_code=400, detail="League has no simulator team"
        )
    results = {"qb": [], "rb": [], "wr": [], "te": []}
    # Add DST & K after round 7 (turns = teams per round * 7 rounds)
    if league.current_draft_turn > len(league.teams) * 7:
        results["dst"] = []
        results["k"] = []

    # Train the logistic regression model
    draft_pick_model = fit_logistic_regression_model(
        league.logistic_regression_variables
    )

    # Begin the simulation
    start_time = time.time()
    i = 0
    while time.time() - start_time < seconds:
        for position in results.keys():
            possible_players = [
                player
                for player in league.players.__getattribute__(position)
                if player.drafted == False
            ]
            if len(possible_players) == 0:
                continue  # No players left; average the samples we have
            best_player = possible_players[0]
            league_copy = league.model_copy(deep=True)
            draft_player(best_player.name, league_copy)
            simulate_draft(league_copy, draft_pick_model)

            # Append the points for the simulator team
            results[position].append(
                league_copy.teams[simulator_team[0]].randomized_starter_points(
                    distributions=league.position_tier_distributions,
                    max_points=league.position_max_points,
                )
            )
            i += 1

    # Turn the arrays into averages
    for position in results.keys():
        samples = results[position]
        results[position] = (
            round(sum(samples) / len(samples), 2) if samples else 0.0
        )
    results["iterations"] = i
    return MonteCarloSimulationResult(**results)


def compute_draft_results(league: League) -> dict:
    """
    Run each team's randomized starter points 1000x and average them
    """
    results = {}
    for team in league.teams:
        points = [
            team.randomized_starter_points(
                distributions=league.position_tier_distributions,
                max_points=league.position_max_points,
            )
            for _ in range(1000)
        ]
        results[team.name] = round(sum(points) / len(points), 2)
    return results


# Routes
@app.post("/league", response_model=League, tags=["league"])
async def create_league(
    file: UploadFile = File(...),
    name: str = "Fantasy Football League",
    round_size: int = ROUND_SIZE,
    roster_size: int = ROSTER_SIZE,
    snake_draft: bool = SNAKE_DRAFT,
    qb_size: int = 1,
    rb_size: int = 2,
    wr_size: int = 2,
    te_size: int = 1,
    flex_size: int = 1,
    dst_size: int = 1,
    k_size: int = 1,
):
    """
    Read data from a POSTed CSV file and create a league
    """
    position_sizes = PositionSizes(
        qb=qb_size,
        rb=rb_size,
        wr=wr_size,
        te=te_size,
        flex=flex_size,
        dst=dst_size,
        k=k_size,
    )
    data = read_csv_upload(
        await file.read(), {"Name", "Order", "Owner", "Simulator"}
    )
    teams = []
    for row in data:
        teams.append(
            Team(
                name=row["Name"],
                draft_order=row["Order"],
                owner=row["Owner"],
                position_sizes=position_sizes,
                simulator=str(row["Simulator"]).strip().lower()
                in ("true", "1"),
            )
        )
    league = League(
        teams=teams,
        snake_draft=snake_draft,
        name=name,
        round_size=round_size,
        roster_size=roster_size,
        position_sizes=position_sizes,
        created=datetime.now(),
        copy_for_draft=False,
        current_draft_turn=0,
    )
    await engine.save(league)
    return league


@app.get("/league", response_model=List[LeagueSimple], tags=["league"])
async def get_leagues(ready_for_draft: bool = True):
    """
    Get all leagues (default to only leagues that are ready for a draft)
    """
    leagues = await engine.find(League)
    if ready_for_draft:
        leagues = [league for league in leagues if league.ready_for_draft]
    leagues = [league for league in leagues if not league.copy_for_draft]
    return leagues


@app.get("/league/{league_id}", response_model=League, tags=["league"])
async def get_league(league_id: ObjectId):
    """
    Get a league by its ID
    """
    league = await get_a_league_by_id(league_id)
    return league


@app.delete("/league/{league_id}", tags=["league"])
async def delete_league(league_id: ObjectId):
    """
    Delete a league by its ID
    """
    league = await get_a_league_by_id(league_id)
    drafts = await engine.find(Draft, Draft.league == league.id)
    for draft in drafts:
        await engine.delete(draft)
    await engine.delete(league)
    return Response(status_code=204)


@app.get("/league/{league_id}/simulator", response_model=Team, tags=["league"])
async def get_league_simulator(league_id: ObjectId):
    """
    Get the simulator team for a league
    """
    league = await get_a_league_by_id(league_id)
    simulator = [team for team in league.teams if team.simulator]
    if not simulator:
        raise HTTPException(status_code=404, detail="Simulator team not found")
    return simulator[0]


@app.post("/league/{league_id}/draft", response_model=Draft, tags=["league"])
async def create_draft_for_a_league(league_id: ObjectId):
    """
    Start a draft for a league
    """
    league = await get_a_league_by_id(league_id)
    if not league.ready_for_draft:
        raise HTTPException(status_code=400, detail="League is not ready for a draft")

    # Copy the league (without its ID) into a new object in the database
    copied_data = league.model_dump()
    copied_league = League(**{k: v for k, v in copied_data.items() if k != "id"})
    copied_league.copy_for_draft = True
    await engine.save(copied_league)

    # Add the copied league to the draft
    draft = Draft(league=copied_league, created=datetime.now())
    await engine.save(draft)
    return draft


@app.post("/league/{league_id}/player", response_model=League, tags=["player"])
async def add_players_to_league(
    league_id: ObjectId,
    file: UploadFile = File(...),
):
    """
    Add current, draftable players to a league
    """
    league = await get_a_league_by_id(league_id)
    if league.players.players:
        raise HTTPException(
            status_code=400, detail="Players already exist for this league"
        )

    # Read the CSV file and create players
    data = read_csv_upload(
        await file.read(), {"Season", "Player", "Pos", "Team", "Projected FFP"}
    )

    # Everything downstream reads points[str(DRAFT_YEAR)], so reject data
    # for the wrong season now instead of 500ing on later requests
    seasons = {str(row["Season"]) for row in data}
    if str(DRAFT_YEAR) not in seasons:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Players file seasons {sorted(seasons)} do not include "
                f"the configured DRAFT_YEAR ({DRAFT_YEAR})"
            ),
        )

    # Players are looked up by name everywhere, so duplicates would
    # silently collapse into one draftable player
    names = [row["Player"] for row in data]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise HTTPException(
            status_code=422,
            detail=f"Duplicate player names in CSV: {duplicates} "
            "(disambiguate, e.g. append team abbreviation)",
        )
    players = []
    for row in data:
        players.append(
            Player(
                name=row["Player"],
                position=row["Pos"],
                nfl_team=row["Team"],
                drafted=False,
                points={
                    str(row["Season"]): PlayerPoints(
                        projected_points=row["Projected FFP"],
                        actual_points=row.get("Actual FFP", None),
                    )
                },
            )
        )
    league.players = Players(players=players)

    # Set the max points for each position
    league.position_max_points = create_max_points(league.players)
    league.ready_position_max_points = True

    # Save and return the league
    await engine.save(league)
    return league


@app.get("/league/{league_id}/player", response_model=Players, tags=["player"])
async def get_players(league_id: ObjectId, draftable_only: bool = True):
    """
    Get all players in a league
    """
    league = await get_a_league_by_id(league_id)
    players = league.players

    # Before returning the data, filter out drafted players if requested
    if draftable_only:
        return Players(
            players=[p for p in players.players if not p.drafted]
        )
    else:
        return players


@app.delete("/league/{league_id}/player", tags=["player"])
async def delete_players_from_league(league_id: ObjectId):
    """
    Delete all players from a league
    """
    league = await get_a_league_by_id(league_id)
    league.players = Players()
    await engine.save(league)
    return Response(status_code=204)


@app.get(
    "/league/{league_id}/player/{player_name}", response_model=Player, tags=["player"]
)
async def get_player(league_id: ObjectId, player_name: str):
    """
    Get a player by their name
    """
    league = await get_a_league_by_id(league_id)
    player = [p for p in league.players.players if p.name == player_name]
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player[0]


async def get_latest_blend(season: int, scoring_format: str) -> BlendedRanking:
    """
    Get the most recently generated blend for a season/format
    """
    blend = await engine.find_one(
        BlendedRanking,
        (BlendedRanking.season == season)
        & (BlendedRanking.scoring_format == scoring_format),
        sort=query.desc(BlendedRanking.generated_at),
    )
    if not blend:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No blended rankings for season {season} "
                f"({scoring_format}); POST /rankings/refresh first"
            ),
        )
    return blend


@app.post("/rankings/refresh", tags=["rankings"])
async def refresh_player_rankings(
    season: int = DRAFT_YEAR, scoring_format: str = SCORING_FORMAT, sources: str = ""
):
    """
    Fetch all (or a comma-separated subset of) configured ranking sources,
    store each source's batch, and generate a new blend. Sources fail
    independently: a broken source is recorded and left out of the blend.
    """
    source_list = [name.strip() for name in sources.split(",") if name.strip()]
    try:
        return await ranking_service.refresh_rankings(
            engine, season, scoring_format, sources=source_list or None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/rankings/blended", response_model=BlendedRanking, tags=["rankings"])
async def get_blended_rankings(
    season: int = DRAFT_YEAR, scoring_format: str = SCORING_FORMAT
):
    """
    Get the most recent blended ranking for a season and scoring format
    """
    return await get_latest_blend(season, scoring_format)


@app.post("/rankings/udk", tags=["rankings"])
async def upload_udk_rankings(
    file: UploadFile = File(...),
    season: int = DRAFT_YEAR,
    scoring_format: str = SCORING_FORMAT,
):
    """
    Ingest a Fantasy Footballers Ultimate Draft Kit CSV export — the
    deliberate file-drop source (login-walled paid content is not
    scraped). Player names resolve against the stored anchor namespace,
    so run POST /rankings/refresh at least once before uploading; the
    blend is regenerated immediately to include the upload.
    """
    from data_sources.udk import parse_udk_rows
    from models.sources import SourceRankingBatch

    rows = read_csv_upload(await file.read(), set())
    records, problems = parse_udk_rows(rows)
    if problems:
        raise HTTPException(
            status_code=422, detail=f"UDK export not usable: {problems}"
        )
    batch = SourceRankingBatch(
        source="udk",
        season=season,
        scoring_format=scoring_format,
        fetched_at=datetime.now(),
        success=True,
        records=[
            {
                "raw_name": record.raw_name,
                "position": record.position,
                "nfl_team": record.nfl_team,
                "rank": record.rank,
                "position_rank": record.position_rank,
                "tier": record.tier,
                "projection": record.projection,
            }
            for record in records
        ],
    )
    summary = await ranking_service.ingest_push_batch(engine, batch)
    if not summary["batch"]["anchored"]:
        summary["warning"] = (
            "No anchor rankings stored yet, so no names could be resolved; "
            "POST /rankings/refresh, then re-upload this file"
        )
    return summary


@app.get("/rankings/status", tags=["rankings"])
async def get_rankings_status(
    season: int = DRAFT_YEAR, scoring_format: str = SCORING_FORMAT
):
    """
    Per-source freshness and configuration: last attempt, last success,
    staleness age, and what the current blend was built from
    """
    return await ranking_service.source_status(engine, season, scoring_format)


@app.post("/owners/ingest/{espn_league_id}", tags=["owners"])
async def ingest_owner_history(
    espn_league_id: int, seasons: str = "", rebuild_profiles: bool = True
):
    """
    Pull pick-by-pick draft history for one ESPN league (all discoverable
    seasons, or a comma-separated subset), backfill historical ADP from
    FFC, and store per-owner picks. Re-running a season replaces it.
    Private leagues need ESPN_S2/ESPN_SWID configured.
    """
    season_list = [int(s.strip()) for s in seasons.split(",") if s.strip()]
    try:
        summary = await ingest_league_history(
            engine, espn_league_id, seasons=season_list or None
        )
    except SourceFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if rebuild_profiles:
        summary["profiles"] = await profiling.build_owner_profiles(engine)
    return summary


@app.post("/owners/profiles/rebuild", tags=["owners"])
async def rebuild_owner_profiles():
    """
    Recompute every owner tendency profile from stored historical picks
    (auction seasons and keeper picks excluded; aliases applied)
    """
    return await profiling.build_owner_profiles(engine)


@app.get("/owners", tags=["owners"])
async def list_owners():
    """
    Every owner seen in ingested history: identity, coverage, and
    whether a tendency profile exists — the review surface for deciding
    which GUIDs to merge via aliases
    """
    picks = await engine.find(HistoricalPick)
    alias_map = await profiling.load_alias_map(engine)
    profiles = {p.profile_key for p in await engine.find(OwnerProfile)}
    owners = {}
    for pick in picks:
        if not pick.member_guid:
            continue
        profile_key = alias_map.get(pick.member_guid, pick.member_guid)
        entry = owners.setdefault(
            profile_key,
            {
                "profile_key": profile_key,
                "member_guids": set(),
                "display_names": set(),
                "espn_league_ids": set(),
                "seasons": set(),
                "picks": 0,
            },
        )
        entry["member_guids"].add(pick.member_guid)
        if pick.owner_display_name:
            entry["display_names"].add(pick.owner_display_name)
        entry["espn_league_ids"].add(pick.espn_league_id)
        entry["seasons"].add(pick.season)
        entry["picks"] += 1
    return sorted(
        (
            {
                "profile_key": entry["profile_key"],
                "member_guids": sorted(entry["member_guids"]),
                "display_names": sorted(entry["display_names"]),
                "espn_league_ids": sorted(entry["espn_league_ids"]),
                "seasons": sorted(entry["seasons"]),
                "picks": entry["picks"],
                "has_profile": entry["profile_key"] in profiles,
            }
            for entry in owners.values()
        ),
        key=lambda entry: -entry["picks"],
    )


@app.get("/owners/{profile_key}/profile", response_model=OwnerProfile, tags=["owners"])
async def get_owner_profile(profile_key: str):
    """
    One owner's tendency profile — inspectable JSON, so 'does this match
    how they actually draft' can be eyeballed before Phase 4 ever feeds
    it to the simulator
    """
    profile = await engine.find_one(
        OwnerProfile, OwnerProfile.profile_key == profile_key
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Owner profile not found")
    return profile


@app.post("/owners/alias", tags=["owners"])
async def create_owner_alias(
    member_guid: str,
    profile_key: str,
    display_name: str = "",
    note: str = "",
    rebuild_profiles: bool = True,
):
    """
    Merge an ESPN member GUID into a profile key (the same human across
    accounts/leagues/co-owned teams). Re-posting a GUID updates it.
    """
    existing = await engine.find_one(
        OwnerAlias, OwnerAlias.member_guid == member_guid
    )
    if existing:
        existing.profile_key = profile_key
        existing.display_name = display_name or existing.display_name
        existing.note = note or existing.note
        alias = existing
    else:
        alias = OwnerAlias(
            member_guid=member_guid,
            profile_key=profile_key,
            display_name=display_name or None,
            note=note or None,
        )
    await engine.save(alias)
    result = {
        "member_guid": alias.member_guid,
        "profile_key": alias.profile_key,
    }
    if rebuild_profiles:
        result["profiles"] = await profiling.build_owner_profiles(engine)
    return result


@app.post("/league/{league_id}/player/sync", response_model=League, tags=["player"])
async def sync_players_from_blended_rankings(
    league_id: ObjectId, scoring_format: str = SCORING_FORMAT
):
    """
    Materialize the latest blend into the league's draftable players —
    the no-CSV replacement for POST /league/{league_id}/player. Unlike
    the upload route this replaces existing players (re-sync friendly);
    leagues used for live drafting are copies, so replacement is safe.
    """
    league = await get_a_league_by_id(league_id)
    blend = await get_latest_blend(DRAFT_YEAR, scoring_format)

    players = []
    seen = set()
    for record in blend.records:  # already sorted best-first
        if record.position not in ["qb", "rb", "wr", "te", "dst", "k"]:
            continue
        # The simulator runs on projected points, so a record no source
        # projected (e.g. ADP-only deep sleepers) cannot be materialized
        if record.blended_projection is None:
            continue
        if record.canonical_name in seen:
            continue
        seen.add(record.canonical_name)
        players.append(
            Player(
                name=record.canonical_name,
                position=record.position,
                nfl_team=record.nfl_team or "",
                drafted=False,
                points={
                    str(DRAFT_YEAR): PlayerPoints(
                        projected_points=record.blended_projection
                    )
                },
                adp=record.adp,
                consensus_rank=record.consensus_rank,
                tier=record.tier,
                source_values=record.source_values,
            )
        )
    if not players:
        raise HTTPException(
            status_code=400,
            detail=(
                "Latest blend has no records with blended projections; "
                "refresh with at least one projection-bearing source "
                "(sleeper, espn)"
            ),
        )

    # A pool smaller than the draft would run out of players mid-simulation
    total_picks = league.round_size * len(league.teams)
    if len(players) < total_picks:
        print(
            f"WARNING: sync for league '{league.name}' materialized only "
            f"{len(players)} players, but the draft needs {total_picks} picks "
            f"({league.round_size} rounds x {len(league.teams)} teams). "
            "Add projection-bearing sources or check the blend."
        )

    league.players = Players(players=players)
    league.position_max_points = create_max_points(league.players)
    league.ready_position_max_points = True
    await engine.save(league)
    return league


@app.post(
    "/league/{league_id}/historical_player",
    response_model=League,
    tags=["historical_player"],
)
async def add_historical_player_data_to_league(
    league_id: ObjectId, file: UploadFile = File(...)
):
    """
    Add historical players to a league to determine position tier distributions
    """
    league = await get_a_league_by_id(league_id)
    if league.ready_position_tier_distributions:
        raise HTTPException(
            status_code=400, detail="Historical players already exist for this league"
        )

    # Read the CSV file and create players
    data = read_csv_upload(
        await file.read(),
        {"Season", "Player", "Pos", "Team", "Projected FFP", "Actual FFP"},
    )
    players = []
    for row in data:
        players.append(
            Player(
                name=row["Player"],
                position=row["Pos"],
                nfl_team=row["Team"],
                drafted=False,
                points={
                    str(row["Season"]): PlayerPoints(
                        projected_points=row["Projected FFP"],
                        actual_points=row.get("Actual FFP", None),
                    )
                },
            )
        )
    league.position_tier_distributions = create_historical_distributions(
        Players(players=players)
    )
    league.ready_position_tier_distributions = True
    await engine.save(league)
    return league


@app.get(
    "/league/{league_id}/historical_player",
    response_model=PositionTierDistributions,
    tags=["historical_player"],
)
async def get_historical_player_data_from_league(league_id: ObjectId):
    """
    Get all historical player data from a league
    """
    league = await get_a_league_by_id(league_id)
    return league.position_tier_distributions


@app.delete(
    "/league/{league_id}/historical_player",
    tags=["historical_player"],
)
async def delete_historical_player_data_from_league(league_id: ObjectId):
    """
    Delete all historical player data from a league
    """
    league = await get_a_league_by_id(league_id)
    league.position_tier_distributions = PositionTierDistributions()
    await engine.save(league)
    return Response(status_code=204)


@app.post(
    "/league/{league_id}/historical_draft",
    response_model=League,
    tags=["historical_draft"],
)
async def add_historical_draft_data_to_league(
    league_id: ObjectId, file: UploadFile = File(...)
):
    """
    Add historical draft data to a league to train the logistic regression model
    """
    league = await get_a_league_by_id(league_id)
    if (
        league.logistic_regression_variables.x
        and league.logistic_regression_variables.y
    ):
        raise HTTPException(
            status_code=400,
            detail="Historical draft data already exists for this league",
        )

    # Read the CSV file and create logistic regression variables
    data = read_csv_upload(await file.read(), {"Pick", "Pos"})
    x = []
    y = []
    for row in data:
        x.append(row["Pick"])
        y.append(row["Pos"])
    league.logistic_regression_variables = LogisticRegressionVariables(x=x, y=y)

    # Sanity check: warn if the historical draft's pick numbers don't roughly
    # match this league's round_size/team count, since a silent mismatch here
    # means the simulator is tuned to a differently-shaped draft than yours
    expected_picks_per_draft = league.round_size * len(league.teams)
    max_pick = max((int(pick) for pick in x), default=0)
    if expected_picks_per_draft and max_pick > expected_picks_per_draft:
        print(
            f"WARNING: historical_draft upload for league '{league.name}' has a "
            f"max pick of {max_pick}, but this league is configured for only "
            f"{expected_picks_per_draft} picks per draft ({league.round_size} rounds x "
            f"{len(league.teams)} teams). Check round_size/roster_size in "
            f"backend/models/config.py against your actual league settings."
        )

    await engine.save(league)
    return league


@app.get(
    "/league/{league_id}/historical_draft",
    response_model=LogisticRegressionVariables,
    tags=["historical_draft"],
)
async def get_historical_draft_data_from_league(league_id: ObjectId):
    """
    Get all historical draft data from a league
    """
    league = await get_a_league_by_id(league_id)
    return league.logistic_regression_variables


@app.delete(
    "/league/{league_id}/historical_draft",
    tags=["historical_draft"],
)
async def delete_historical_draft_data_from_league(league_id: ObjectId):
    """
    Delete all historical draft data from a league
    """
    league = await get_a_league_by_id(league_id)
    league.logistic_regression_variables = LogisticRegressionVariables()
    await engine.save(league)
    return Response(status_code=204)


@app.get("/draft/{draft_id}", response_model=Draft, tags=["draft"])
async def get_draft(draft_id: ObjectId):
    """
    Get a draft by its ID
    """
    draft = await get_a_draft_by_id(draft_id)
    return draft


@app.get("/draft", response_model=List[DraftSimple], tags=["draft"])
async def get_drafts():
    """
    Get all drafts from leagues that exist
    """
    return await engine.find(Draft)


@app.post("/draft/{draft_id}/pick", response_model=Draft, tags=["draft"])
async def make_draft_pick(
    draft_id: ObjectId, name: str = "", use_simulator: bool = False
):
    """
    Make a draft pick by name or using the simulator
    """
    draft = await get_a_draft_by_id(draft_id)
    if not draft.league.draft_order:
        raise HTTPException(status_code=400, detail="Draft is complete")
    if name and use_simulator:
        raise HTTPException(
            status_code=400, detail="Cannot include a name and use the simulator"
        )
    if not name and not use_simulator:
        raise HTTPException(
            status_code=400, detail="Must include a name or use the simulator"
        )

    # If using the simulator, get a pick name
    if use_simulator:
        draft_pick_model = fit_logistic_regression_model(
            draft.league.logistic_regression_variables
        )
        name = simulate_pick(draft.league, draft_pick_model)

    # Find the player picked by name
    players = draft.league.players.players
    player = [player for player in players if player.name == name]
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    else:
        player = player[0]

    # Set the player as drafted within the league
    draft_player(name, draft.league)

    # Save the draft
    await engine.save(draft)

    # Return the draft after all operations have been performed
    return draft


# Run a Monte Carlo simulation to determine the best position to draft
@app.post(
    "/draft/{draft_id}/monte_carlo",
    response_model=MonteCarloSimulationResult,
    tags=["draft"],
)
async def run_monte_carlo_simulation(draft_id: ObjectId):
    """
    Run a Monte Carlo simulation to determine the best position to draft
    """
    draft = await get_a_draft_by_id(draft_id)
    if not draft.league.draft_order:
        raise HTTPException(status_code=400, detail="Draft is complete")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(process_pool, monte_carlo_draft, draft.league)


# Get the results of a draft by running each team's randomized points 1000x times
@app.get(
    "/draft/{draft_id}/results",
    response_model=dict,  # This is just a dictionary with team names as keys and points as values
    tags=["draft"],
)
async def get_draft_results(draft_id: ObjectId):
    """
    Get the results of a draft by running each team's randomized points 1000x times
    """
    draft = await get_a_draft_by_id(draft_id)
    return await run_in_threadpool(compute_draft_results, draft.league)
