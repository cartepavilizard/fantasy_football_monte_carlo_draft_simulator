# -*- coding: utf-8 -*-
"""
C6: the lineup-locking decision rules. Rule 1 (arrangement) is free EV
and always applied: same optimal total, but early-locking players land
in restrictive slots so flex-type slots stay unlocked longest. Rule 2
(margin) is advice only: a later-kicking bench alternative within the
margin of an early flex occupant gets surfaced with its cost, never
auto-applied. The Wednesday opener needs no special case — everything
keys on kickoff times.
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
from models.lineup import optimize_lineup

SEASON = DRAFT_YEAR
LEAGUE_ID = 111
WEEK = 5

THURSDAY = datetime.datetime(SEASON, 10, 8, 20, 15)
SUNDAY_EARLY = datetime.datetime(SEASON, 10, 11, 13, 0)
SUNDAY_LATE = datetime.datetime(SEASON, 10, 11, 16, 25)
MONDAY = datetime.datetime(SEASON, 10, 12, 20, 15)


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-locking")


def entry(pid, name, position, team, slot, projected):
    return RosterSlotEntry(
        player_id=pid,
        player_name=name,
        position=position,
        nfl_team=team,
        lineup_slot=slot,
        projected_points=projected,
    )


async def seed(engine, slot_counts, entries, games):
    await engine.save(
        InSeasonLeague(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            name="The Family League",
            team_count=1,
            latest_scoring_period=WEEK,
            lineup_slot_counts=slot_counts,
            teams=[LeagueTeamInfo(espn_team_id=1, name="Emerald City Edge")],
        )
    )
    await engine.save(
        TeamWeekRoster(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=WEEK,
            espn_team_id=1,
            entries=entries,
        )
    )
    for home, away, kickoff in games:
        await engine.save(
            ProGame(
                season=SEASON,
                week=WEEK,
                home_team=home,
                away_team=away,
                kickoff=kickoff,
            )
        )


def run(engine):
    async def go():
        league = await engine.find_one(
            InSeasonLeague, InSeasonLeague.espn_league_id == LEAGUE_ID
        )
        return await optimize_lineup(engine, league, 1, WEEK)

    return asyncio.run(go())


def slot_of(result, player_name):
    for slot_entry in result["optimal"]:
        player = slot_entry["player"]
        if player and player["player_name"] == player_name:
            return slot_entry["slot"]
    return None


def test_arrangement_puts_the_early_locker_in_the_restrictive_slot():
    """Same starters either way — the Thursday RB must hold the RB slot
    so FLEX stays open until Sunday. Roster order is chosen so a naive
    first-found tiebreak would do the opposite."""
    engine = make_engine()

    async def go():
        await seed(
            engine,
            {"RB": 1, "FLEX": 1, "BE": 2},
            [
                entry(1, "Sunday RB", "RB", "KC", "RB", 12.0),
                entry(2, "Thursday RB", "RB", "SEA", "FLEX", 15.0),
            ],
            [("SEA", "ARI", THURSDAY), ("KC", "BUF", SUNDAY_LATE)],
        )

    asyncio.run(go())
    result = run(engine)
    assert result["optimal_total"] == 27.0  # arrangement never costs points
    assert slot_of(result, "Thursday RB") == "RB"
    assert slot_of(result, "Sunday RB") == "FLEX"


def test_wednesday_opener_is_just_an_earlier_kickoff():
    wednesday = datetime.datetime(SEASON, 9, 9, 19, 20)
    engine = make_engine()

    async def go():
        await seed(
            engine,
            {"WR": 1, "FLEX": 1, "BE": 2},
            [
                entry(1, "Sunday WR", "WR", "KC", "WR", 11.0),
                entry(2, "Wednesday WR", "WR", "SEA", "FLEX", 11.5),
            ],
            [("SEA", "ARI", wednesday), ("KC", "BUF", SUNDAY_LATE)],
        )

    asyncio.run(go())
    result = run(engine)
    assert slot_of(result, "Wednesday WR") == "WR"
    assert slot_of(result, "Sunday WR") == "FLEX"


def test_margin_rule_flags_a_cheap_later_alternative():
    engine = make_engine()

    async def go():
        await seed(
            engine,
            {"RB": 1, "FLEX": 1, "BE": 2},
            [
                entry(1, "Best RB", "RB", "GB", "RB", 18.0),
                entry(2, "Thursday Flex", "RB", "SEA", "FLEX", 10.0),
                entry(3, "Sunday Bench", "RB", "KC", "BE", 9.2),
            ],
            [
                ("SEA", "ARI", THURSDAY),
                ("KC", "BUF", SUNDAY_EARLY),
                ("GB", "DET", MONDAY),
            ],
        )

    asyncio.run(go())
    result = run(engine)
    # rule 1 already tucked the Thursday starter into the dedicated RB
    # slot (Best RB, who plays Monday, holds FLEX)...
    assert slot_of(result, "Thursday Flex") == "RB"
    assert slot_of(result, "Best RB") == "FLEX"
    # ...and rule 2 quantifies the start/sit option that remains
    (advice,) = result["lock_advice"]
    assert advice["slot"] == "RB"
    assert advice["start"] == 2
    assert advice["alternative"] == 3
    assert advice["cost_points"] == 0.8
    assert "keeps this slot open" in advice["note"]


def test_no_advice_beyond_the_margin_or_for_late_occupants():
    engine = make_engine()

    async def go():
        await seed(
            engine,
            {"RB": 1, "FLEX": 1, "BE": 2},
            [
                entry(1, "Best RB", "RB", "GB", "RB", 18.0),
                # occupant locks Thursday but the bench gap is 2.5 pts
                entry(2, "Thursday Flex", "RB", "SEA", "FLEX", 10.0),
                entry(3, "Sunday Bench", "RB", "KC", "BE", 7.5),
            ],
            [
                ("SEA", "ARI", THURSDAY),
                ("KC", "BUF", SUNDAY_EARLY),
                ("GB", "DET", MONDAY),
            ],
        )

    asyncio.run(go())
    assert run(engine)["lock_advice"] == []

    engine2 = make_engine()

    async def go2():
        await seed(
            engine2,
            {"RB": 1, "FLEX": 1, "BE": 2},
            [
                entry(1, "Best RB", "RB", "GB", "RB", 18.0),
                # flex occupant is Sunday: nothing locks early, no advice
                entry(2, "Sunday Flex", "RB", "KC", "FLEX", 10.0),
                entry(3, "Monday Bench", "RB", "GB", "BE", 9.8),
            ],
            [("KC", "BUF", SUNDAY_EARLY), ("GB", "DET", MONDAY)],
        )

    asyncio.run(go2())
    assert run(engine2)["lock_advice"] == []


def test_advice_covers_dedicated_slots_too():
    """The margin rule is about start/sit optionality, not slot shape:
    an early RB in the RB slot with a near-equal Sunday bench RB is
    still a decision worth keeping open"""
    engine = make_engine()

    async def go():
        await seed(
            engine,
            {"RB": 1, "BE": 2},
            [
                entry(1, "Thursday RB", "RB", "SEA", "RB", 10.0),
                entry(2, "Sunday Bench", "RB", "KC", "BE", 9.5),
            ],
            [("SEA", "ARI", THURSDAY), ("KC", "BUF", SUNDAY_EARLY), ("GB", "DET", MONDAY)],
        )

    asyncio.run(go())
    (advice,) = run(engine)["lock_advice"]
    assert advice["slot"] == "RB"
    assert advice["cost_points"] == 0.5
