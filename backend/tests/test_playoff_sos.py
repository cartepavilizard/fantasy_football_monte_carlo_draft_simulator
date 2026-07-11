# -*- coding: utf-8 -*-
"""
C5: playoff (weeks 14-16) strength of schedule. Re-slices C2's
opponent-vs-position table into the playoff window — these tests pin
down the sum-not-average bye handling, the weakest-of-window confidence
rollup, C2's early-season neutral note carrying through unchanged, and
the optional per-league roster join.
"""
import asyncio
import datetime

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.config import DRAFT_YEAR
from models.inseason import (
    InSeasonLeague,
    LeagueTeamInfo,
    ProGame,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.playoff_sos import playoff_schedule_strength, playoff_sos_for_league

SEASON = DRAFT_YEAR


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-playoff-sos")


def game(engine, week, home, away):
    return engine.save(
        ProGame(
            season=SEASON,
            week=week,
            home_team=home,
            away_team=away,
            kickoff=datetime.datetime(SEASON, 9, 10 + week),
        )
    )


def roster(engine, league_id, week, team_id, entries):
    """entries: list of (player_name, position, nfl_team, actual, slot)"""
    return engine.save(
        TeamWeekRoster(
            espn_league_id=league_id,
            season=SEASON,
            week=week,
            espn_team_id=team_id,
            entries=[
                RosterSlotEntry(
                    player_id=abs(hash((name, week))) % 10_000,
                    player_name=name,
                    position=position,
                    nfl_team=nfl_team,
                    lineup_slot=slot,
                    actual_points=actual,
                )
                for name, position, nfl_team, actual, slot in entries
            ],
        )
    )


# The same week-1 sample as test_matchup_strength.py: four defenses get
# a known RB multiplier (ARI softest at 1.2, LAR toughest at 0.85, BUF
# and GB neutral at 1.0). SEA/KC/DET/SF were the offenses, never sampled
# as defenses, so they fall back to neutral (confidence "none") when
# looked up as a defense.
WEEK1_GAMES = [("SEA", "ARI"), ("KC", "BUF"), ("DET", "GB"), ("SF", "LAR")]


def seed_known_defenses(engine):
    """League + week-1 completed slate producing the RB multipliers above"""

    async def go():
        await engine.save(
            InSeasonLeague(
                espn_league_id=111,
                season=SEASON,
                name="League 111",
                team_count=2,
                latest_scoring_period=2,  # week 1 complete
            )
        )
        for home, away in WEEK1_GAMES:
            await game(engine, 1, home, away)
        await roster(
            engine,
            111,
            1,
            1,
            [
                ("RB SEA", "RB", "SEA", 20.0, "BE"),
                ("RB KC", "RB", "KC", 10.0, "BE"),
                ("RB DET", "RB", "DET", 10.0, "BE"),
                ("RB SF", "RB", "SF", 0.0, "BE"),
            ],
        )

    asyncio.run(go())


def seed_playoff_window(engine):
    """Weeks 14-16 games: SEA/KC/DET (teams of interest) face the four
    known defenses above; some weeks are byes on purpose."""

    async def go():
        for week, home, away in [
            (14, "SEA", "ARI"),
            (14, "KC", "GB"),
            (14, "DET", "LAR"),
            (15, "SEA", "BUF"),
            (15, "KC", "LAR"),
            (15, "DET", "SF"),
            (16, "KC", "ARI"),
        ]:
            await game(engine, week, home, away)

    asyncio.run(go())


def test_playoff_scores_sum_multipliers_and_report_byes_and_confidence():
    engine = make_engine()
    seed_known_defenses(engine)
    seed_playoff_window(engine)

    sos = asyncio.run(playoff_schedule_strength(engine, SEASON))
    rb = sos["positions"]["RB"]

    # SEA: ARI (1.2) + BUF (1.0), bye week 16
    sea = rb["SEA"]
    assert sea["score"] == 2.2
    assert sea["games_scheduled"] == 2
    assert sea["bye_weeks"] == [16]
    assert sea["confidence"] == "low"  # both opponent-weeks are low-confidence

    # KC: GB (1.0) + LAR (0.85) + ARI (1.2), no bye
    kc = rb["KC"]
    assert kc["score"] == 3.05
    assert kc["games_scheduled"] == 3
    assert kc["bye_weeks"] == []
    assert kc["rank"] == 1  # highest sum = easiest schedule

    # DET: LAR (0.85, low) + SF (neutral 1.0, confidence "none"), bye week 16
    det = rb["DET"]
    assert det["score"] == 1.85
    assert det["games_scheduled"] == 2
    assert det["bye_weeks"] == [16]
    assert det["confidence"] == "none"  # weakest of [low, none] is none

    assert sea["rank"] == 2
    assert kc["rank"] < sea["rank"] < det["rank"]


def test_no_completed_weeks_is_all_neutral_and_the_note_says_so():
    """Early-season call: no regular-season data yet, only the playoff
    window's own schedule exists. Every score is games*1.0 and the
    early-season note from C2 carries through unchanged."""
    engine = make_engine()
    seed_playoff_window(engine)  # no seed_known_defenses() this time

    sos = asyncio.run(playoff_schedule_strength(engine, SEASON))

    assert sos["note"] is not None
    assert "neutral" in sos["note"]
    sea = sos["positions"]["RB"]["SEA"]
    assert sea["score"] == 2.0  # two scheduled games, both neutral 1.0
    assert sea["confidence"] == "none"


def test_weeks_window_is_configurable():
    engine = make_engine()
    seed_known_defenses(engine)
    seed_playoff_window(engine)

    sos = asyncio.run(playoff_schedule_strength(engine, SEASON, weeks=[14]))
    assert sos["weeks"] == [14]
    sea = sos["positions"]["RB"]["SEA"]
    assert sea["score"] == 1.2  # only week 14's ARI game counts
    assert sea["games_scheduled"] == 1


def test_playoff_sos_for_league_joins_current_starters_and_averages_ranks():
    engine = make_engine()
    seed_known_defenses(engine)
    seed_playoff_window(engine)

    async def go():
        league = InSeasonLeague(
            espn_league_id=222,
            season=SEASON,
            name="Family League",
            team_count=1,
            latest_scoring_period=5,
            teams=[LeagueTeamInfo(espn_team_id=1, name="Team One")],
        )
        await engine.save(league)
        await roster(
            engine,
            222,
            5,
            1,
            [
                ("Kenneth Walker", "RB", "SEA", None, "RB"),  # SEA rank 2
                ("Bench Guy", "RB", "KC", None, "BE"),  # excluded: bench
                ("Hurt Guy", "RB", "DET", None, "IR"),  # excluded: IR
                ("Mystery Guy", "RB", None, None, "FLEX"),  # no nfl_team
                ("KC Runner", "RB", "KC", None, "RB"),  # KC rank 1
            ],
        )
        sos = await playoff_schedule_strength(engine, SEASON)
        return league, await playoff_sos_for_league(engine, league, SEASON, sos)

    league, teams = asyncio.run(go())
    (team,) = teams
    assert team["espn_team_id"] == 1
    assert team["team_name"] == "Team One"
    starter_names = [starter["player_name"] for starter in team["starters"]]
    assert starter_names == ["Kenneth Walker", "Mystery Guy", "KC Runner"]

    mystery = next(s for s in team["starters"] if s["player_name"] == "Mystery Guy")
    assert mystery["playoff_sos"] is None

    walker = next(s for s in team["starters"] if s["player_name"] == "Kenneth Walker")
    runner = next(s for s in team["starters"] if s["player_name"] == "KC Runner")
    assert walker["playoff_sos"]["rank"] == 2
    assert runner["playoff_sos"]["rank"] == 1
    # average of ranks 2 and 1 — Mystery Guy's null playoff_sos is excluded
    assert team["average_rank"] == 1.5


# --- endpoint --------------------------------------------------------------


def test_playoff_sos_endpoint_position_filter_and_404(client, app_module):
    engine = app_module.engine
    seed_known_defenses(engine)
    seed_playoff_window(engine)

    payload = client.get("/inseason/playoff_sos?position=rb").json()
    assert list(payload["positions"].keys()) == ["RB"]
    assert payload["positions"]["RB"]["KC"]["rank"] == 1

    assert client.get("/inseason/playoff_sos?position=XX").status_code == 404


def test_playoff_sos_endpoint_scoped_to_league_adds_rosters(client, app_module):
    engine = app_module.engine
    seed_known_defenses(engine)
    seed_playoff_window(engine)

    async def seed_league():
        await engine.save(
            InSeasonLeague(
                espn_league_id=333,
                season=SEASON,
                name="Scoped League",
                team_count=1,
                latest_scoring_period=5,
                teams=[LeagueTeamInfo(espn_team_id=9, name="Team Nine")],
            )
        )
        await roster(
            engine,
            333,
            5,
            9,
            [("KC Runner", "RB", "KC", None, "RB")],
        )

    asyncio.run(seed_league())

    payload = client.get("/inseason/playoff_sos?espn_league_id=333").json()
    (team,) = payload["rosters"]
    assert team["team_name"] == "Team Nine"
    assert team["starters"][0]["playoff_sos"]["rank"] == 1

    assert client.get("/inseason/playoff_sos?espn_league_id=999999").status_code == 404
