# -*- coding: utf-8 -*-
"""
C2: opponent-vs-position matchup strength. The methodology contract in
models/matchup_strength.py is what these tests pin down: per-league-week
ratio normalization, cross-league averaging inside a week (coverage, not
extra evidence), shrinkage toward neutral with the fixed prior (week 1
is exactly neutral by construction), sample guardrails, and the capped
projection tilt C1 applies.
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.config import DRAFT_YEAR
from models.inseason import (
    InSeasonLeague,
    ProGame,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.matchup_strength import (
    MIN_DEFENSES_SAMPLED,
    defense_position_strength,
    matchup_adjusted,
    strength_for,
)

SEASON = DRAFT_YEAR
import datetime


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-matchups")


def league(engine, league_id, latest_week):
    return engine.save(
        InSeasonLeague(
            espn_league_id=league_id,
            season=SEASON,
            name=f"League {league_id}",
            team_count=2,
            latest_scoring_period=latest_week,
        )
    )


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
    """entries: list of (player_name, position, nfl_team, actual_points)"""
    return engine.save(
        TeamWeekRoster(
            espn_league_id=league_id,
            season=SEASON,
            week=week,
            espn_team_id=team_id,
            entries=[
                RosterSlotEntry(
                    player_id=abs(hash(name)) % 10_000,
                    player_name=name,
                    position=position,
                    nfl_team=nfl_team,
                    lineup_slot="BE",
                    actual_points=actual,
                )
                for name, position, nfl_team, actual in entries
            ],
        )
    )


# Four week-1 games -> eight teams; every RB faces a distinct defense.
WEEK1_GAMES = [("SEA", "ARI"), ("KC", "BUF"), ("DET", "GB"), ("SF", "LAR")]


def seed_week(engine_ops, league_id, week, rb_points_by_team):
    """One completed week in one league: one RB per NFL team with the
    given actual points (defense faced comes from the schedule)"""

    async def go():
        entries = [
            (f"RB {team} w{week} L{league_id}", "RB", team, points)
            for team, points in rb_points_by_team.items()
        ]
        await roster(engine_ops, league_id, week, 1, entries)

    return go()


def test_week_one_is_fully_neutral():
    """Before any completed week exists, every lookup is neutral and the
    table says so — the September small-sample story, by construction"""
    engine = make_engine()

    async def go():
        await league(engine, 111, latest_week=1)
        for home, away in WEEK1_GAMES:
            await game(engine, 1, home, away)
        # week 1 rosters exist (in progress) but the week is not complete
        await roster(engine, 111, 1, 1, [("Some RB", "RB", "SEA", 22.0)])
        return await defense_position_strength(engine, SEASON)

    strength = asyncio.run(go())
    assert strength["positions"]["RB"] == {}
    assert "neutral" in strength["note"]
    neutral = strength_for(strength, "RB", "ARI")
    assert neutral["multiplier"] == 1.0
    assert neutral["confidence"] == "none"


def test_single_week_ratios_are_shrunk_toward_neutral():
    engine = make_engine()
    # Four defenses sampled; ARI allows double the mean, LAR none.
    points = {"SEA": 20.0, "KC": 10.0, "DET": 10.0, "SF": 0.0}
    # defenses faced: SEA->ARI, KC->BUF, DET->GB, SF->LAR; mean = 10

    async def go():
        await league(engine, 111, latest_week=2)  # week 1 complete
        for home, away in WEEK1_GAMES:
            await game(engine, 1, home, away)
        await seed_week(engine, 111, 1, points)
        return await defense_position_strength(engine, SEASON)

    strength = asyncio.run(go())
    ari = strength["positions"]["RB"]["ARI"]
    # observed ratio 2.0, one week of evidence vs a 4-week neutral prior:
    # (1*2.0 + 4*1.0) / 5 = 1.2
    assert ari["observed_ratio"] == 2.0
    assert ari["multiplier"] == 1.2
    assert ari["weeks_sampled"] == 1
    assert ari["confidence"] == "low"
    assert ari["rank"] == 1  # rank 1 = allows the most points
    # LAR allowed nothing: ratio clamps at 0.25 -> (0.25 + 4) / 5 = 0.85
    lar = strength["positions"]["RB"]["LAR"]
    assert lar["multiplier"] == 0.85
    assert lar["rank"] == 4


def test_leagues_average_within_a_week_not_as_extra_weeks():
    """Two leagues sampling the same NFL week is better coverage of the
    same games — n stays 1 and their ratios average"""
    engine = make_engine()

    async def go():
        await league(engine, 111, latest_week=2)
        await league(engine, 222, latest_week=2)
        for home, away in WEEK1_GAMES:
            await game(engine, 1, home, away)
        # league 111 saw ARI allow 2x the mean; league 222 saw 1x
        await seed_week(
            engine, 111, 1, {"SEA": 20.0, "KC": 10.0, "DET": 10.0, "SF": 0.0}
        )
        await seed_week(
            engine, 222, 1, {"SEA": 10.0, "KC": 10.0, "DET": 10.0, "SF": 10.0}
        )
        return await defense_position_strength(engine, SEASON)

    strength = asyncio.run(go())
    ari = strength["positions"]["RB"]["ARI"]
    assert ari["weeks_sampled"] == 1  # NOT 2 — same NFL week
    assert ari["observed_ratio"] == 1.5  # mean of 2.0 and 1.0
    # (1*1.5 + 4) / 5 = 1.1
    assert ari["multiplier"] == 1.1


def test_more_weeks_earn_more_weight():
    engine = make_engine()
    points = {"SEA": 20.0, "KC": 10.0, "DET": 10.0, "SF": 0.0}

    async def go():
        await league(engine, 111, latest_week=7)  # weeks 1-6 complete
        for week in range(1, 7):
            for home, away in WEEK1_GAMES:
                await game(engine, week, home, away)
            await seed_week(engine, 111, week, points)
        return await defense_position_strength(engine, SEASON)

    strength = asyncio.run(go())
    ari = strength["positions"]["RB"]["ARI"]
    assert ari["weeks_sampled"] == 6
    # (6*2.0 + 4*1.0) / 10 = 1.6, clamped to the 1.3 ceiling
    assert ari["multiplier"] == 1.3
    assert ari["confidence"] == "high"


def test_thin_samples_are_discarded():
    """A sample covering fewer defenses than the floor says nothing
    about the mean and never counts"""
    engine = make_engine()

    async def go():
        await league(engine, 111, latest_week=2)
        for home, away in WEEK1_GAMES:
            await game(engine, 1, home, away)
        thin = {"SEA": 30.0, "KC": 10.0}  # 2 defenses < MIN_DEFENSES_SAMPLED
        assert len(thin) < MIN_DEFENSES_SAMPLED
        await seed_week(engine, 111, 1, thin)
        return await defense_position_strength(engine, SEASON)

    strength = asyncio.run(go())
    assert strength["positions"]["RB"] == {}


def test_through_week_caps_the_sample_window():
    engine = make_engine()

    async def go():
        await league(engine, 111, latest_week=3)
        for week in (1, 2):
            for home, away in WEEK1_GAMES:
                await game(engine, week, home, away)
        await seed_week(
            engine, 111, 1, {"SEA": 20.0, "KC": 10.0, "DET": 10.0, "SF": 0.0}
        )
        await seed_week(
            engine, 111, 2, {"SEA": 100.0, "KC": 10.0, "DET": 10.0, "SF": 0.0}
        )
        return await defense_position_strength(engine, SEASON, through_week=1)

    strength = asyncio.run(go())
    assert strength["positions"]["RB"]["ARI"]["weeks_sampled"] == 1
    assert strength["positions"]["RB"]["ARI"]["observed_ratio"] == 2.0


def test_matchup_adjusted_is_a_capped_tilt():
    # alpha 0.5: a 1.2 multiplier tilts a 20-pt projection by +10% * 20 * ...
    # 0.5 * (1.2 - 1) = +0.10 -> 22.0
    assert matchup_adjusted(20.0, 1.2, alpha=0.5, max_tilt=0.10) == 22.0
    # the cap binds before a huge multiplier does: 0.5*(1.3-1)=0.15 -> 0.10
    assert matchup_adjusted(20.0, 1.3, alpha=0.5, max_tilt=0.10) == 22.0
    assert matchup_adjusted(20.0, 0.7, alpha=0.5, max_tilt=0.10) == 18.0
    assert matchup_adjusted(20.0, 1.0) == 20.0
    assert matchup_adjusted(None, 1.2) is None


def test_matchup_strength_endpoint_serves_from_cache(client, app_module):
    engine = app_module.engine

    async def seed():
        await league(engine, 111, latest_week=2)
        for home, away in WEEK1_GAMES:
            await game(engine, 1, home, away)
        await seed_week(
            engine, 111, 1, {"SEA": 20.0, "KC": 10.0, "DET": 10.0, "SF": 0.0}
        )

    asyncio.run(seed())
    payload = client.get("/inseason/matchup_strength?position=rb").json()
    assert list(payload["positions"].keys()) == ["RB"]
    assert payload["positions"]["RB"]["ARI"]["multiplier"] == 1.2
    assert client.get("/inseason/matchup_strength?position=XX").status_code == 404
