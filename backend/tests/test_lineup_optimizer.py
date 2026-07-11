# -*- coding: utf-8 -*-
"""
C1: the full lineup optimizer. The assignment must be EXACT (overlapping
flex types are where greedy breaks), the projection source is ESPN's
weekly numbers behind the weekly_projections seam, and C2's tilt is
applied on top. The endpoint inherits B4's cached-only constraint.
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
from models.lineup import (
    best_assignment,
    ensure_lineup_review,
    optimize_lineup,
    slot_instances,
    weekly_projections,
)
from models.notifications import Notification

SEASON = DRAFT_YEAR
LEAGUE_ID = 111
WEEK = 5


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-lineup")


def entry(pid, name, position, team, slot, projected, injury=None):
    return RosterSlotEntry(
        player_id=pid,
        player_name=name,
        position=position,
        nfl_team=team,
        lineup_slot=slot,
        injury_status=injury,
        projected_points=projected,
    )


STANDARD_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "BE": 5, "IR": 1}


async def seed_league(engine, slot_counts=None, latest_week=WEEK):
    league = InSeasonLeague(
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        name="The Family League",
        team_count=2,
        latest_scoring_period=latest_week,
        lineup_slot_counts=slot_counts or STANDARD_SLOTS,
        teams=[
            LeagueTeamInfo(espn_team_id=1, name="Emerald City Edge"),
            LeagueTeamInfo(espn_team_id=2, name="Bro Squad"),
        ],
    )
    await engine.save(league)
    return league


async def seed_games(engine, week=WEEK):
    """Thu SEA-ARI, Sun KC-BUF and DET-GB, Mon SF-LAR"""
    for day, hour, home, away in [
        (8, 20, "SEA", "ARI"),
        (11, 13, "KC", "BUF"),
        (11, 16, "DET", "GB"),
        (12, 20, "SF", "LAR"),
    ]:
        await engine.save(
            ProGame(
                season=SEASON,
                week=week,
                home_team=home,
                away_team=away,
                kickoff=datetime.datetime(SEASON, 10, day, hour, 0),
            )
        )


# --- the exact assignment ------------------------------------------------------


def test_slot_instances_expand_and_order_slots():
    assert slot_instances({"RB": 2, "QB": 1, "FLEX": 1, "BE": 5, "IR": 1}) == [
        "QB",
        "RB",
        "RB",
        "FLEX",
    ]


def test_best_assignment_beats_greedy_on_flex_overlap():
    """Greedy puts the top RB in FLEX and strands the WR; exact doesn't"""
    slots = ["FLEX", "RB"]
    candidates = [(1, "RB"), (2, "WR"), (3, "RB")]
    weights = {1: 12.0, 2: 11.0, 3: 5.0}
    assignment, total = best_assignment(slots, candidates, weights)
    assert total == 23.0
    assert assignment == {0: 2, 1: 1}  # WR in FLEX, top RB at RB


def test_best_assignment_handles_multiple_overlapping_flex_types():
    slots = ["RB/WR", "WR/TE", "WR"]
    candidates = [(1, "WR"), (2, "WR"), (3, "TE"), (4, "RB")]
    weights = {1: 10.0, 2: 9.0, 3: 2.0, 4: 3.0}
    assignment, total = best_assignment(slots, candidates, weights)
    # both WRs start (10 + 9) with the RB in RB/WR: 22 — not TE in WR/TE
    assert total == 22.0
    placed = {candidates[0][0], candidates[1][0], candidates[3][0]}
    assert set(assignment.values()) == placed


def test_best_assignment_leaves_unfillable_slots_empty():
    slots = ["QB", "TE"]
    candidates = [(1, "QB")]
    assignment, total = best_assignment(slots, candidates, {1: 20.0})
    assert assignment == {0: 1}
    assert total == 20.0


def test_zero_projection_body_beats_empty_slot():
    slots = ["TE"]
    candidates = [(1, "TE")]
    assignment, total = best_assignment(slots, candidates, {1: None})
    assert assignment == {0: 1}  # a body over an empty slot
    assert total == 0.0


# --- optimize_lineup end to end --------------------------------------------------


def seed_roster(engine, entries, team_id=1, week=WEEK):
    return engine.save(
        TeamWeekRoster(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=week,
            espn_team_id=team_id,
            entries=entries,
        )
    )


def full_roster_entries():
    return [
        entry(1, "QB One", "QB", "KC", "QB", 22.0),
        entry(2, "RB One", "RB", "SEA", "RB", 15.0),
        entry(3, "RB Two", "RB", "DET", "RB", 12.0),
        entry(4, "WR One", "WR", "BUF", "WR", 14.0),
        entry(5, "WR Two", "WR", "GB", "WR", 11.0),
        entry(6, "TE One", "TE", "SF", "TE", 9.0),
        # the interesting bench: a WR who out-projects the FLEX starter
        entry(7, "RB Three", "RB", "LAR", "FLEX", 8.0),
        entry(8, "WR Three", "WR", "ARI", "BE", 10.5),
        entry(9, "QB Two", "QB", "DET", "BE", 17.0),
        entry(10, "IR Guy", "RB", "KC", "IR", 20.0),
    ]


def run_optimize(engine, team_id=1, week=WEEK, **kwargs):
    async def go():
        league = await engine.find_one(
            InSeasonLeague, InSeasonLeague.espn_league_id == LEAGUE_ID
        )
        return await optimize_lineup(engine, league, team_id, week, **kwargs)

    return asyncio.run(go())


def test_optimize_finds_the_flex_upgrade_and_reports_moves():
    engine = make_engine()

    async def seed():
        await seed_league(engine)
        await seed_games(engine)
        await seed_roster(engine, full_roster_entries())

    asyncio.run(seed())
    result = run_optimize(engine)
    # WR Three (10.5) starts over RB Three (8.0)
    starters = {
        s["player"]["player_name"] for s in result["optimal"] if s["player"]
    }
    assert "WR Three" in starters and "RB Three" not in starters
    assert result["delta_points"] == 2.5
    # C6's arrangement puts Thursday's WR Three in the dedicated WR slot
    # and hands FLEX to a Sunday player — the moves say so
    by_name = {move["player_name"]: move for move in result["moves"]}
    assert by_name["RB Three"] == {
        "player_id": 7,
        "player_name": "RB Three",
        "from_slot": "FLEX",
        "to_slot": "BE",
    }
    assert by_name["WR Three"]["from_slot"] == "BE"
    assert by_name["WR Three"]["to_slot"] == "WR"
    assert by_name["WR Two"]["to_slot"] == "FLEX"
    # IR player is never a candidate, even at 20 projected points
    assert "IR Guy" not in starters
    assert [p["player_id"] for p in result["ir"]] == [10]
    # matchup context annotated per player (neutral: no completed weeks)
    wr_three = next(
        s["player"]
        for s in result["optimal"]
        if s["player"] and s["player"]["player_name"] == "WR Three"
    )
    assert wr_three["opponent"] == "SEA"
    assert wr_three["matchup"]["multiplier"] == 1.0
    assert any("neutral" in w for w in result["warnings"])


def test_projection_seam_overrides_espn_numbers():
    engine = make_engine()

    async def seed():
        await seed_league(engine)
        await seed_games(engine)
        await seed_roster(engine, full_roster_entries())

    asyncio.run(seed())
    # an injected source says RB Three is actually the play
    result = run_optimize(engine, projections={7: 25.0})
    flex = next(s for s in result["optimal"] if s["slot"] == "FLEX")
    assert flex["player"]["player_name"] == "RB Three"
    assert flex["player"]["base_projection"] == 25.0


def test_weekly_projections_reads_the_synced_espn_numbers():
    roster = TeamWeekRoster(
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        week=WEEK,
        espn_team_id=1,
        entries=[entry(1, "QB One", "QB", "KC", "QB", 22.0)],
    )
    assert weekly_projections(roster) == {1: 22.0}


def test_matchup_tilt_moves_the_call():
    """C2 integration: a soft enough matchup flips a close flex call"""
    engine = make_engine()

    async def seed():
        await seed_league(engine)
        await seed_games(engine)
        await seed_roster(engine, full_roster_entries())

    asyncio.run(seed())
    # hand-built strength table: WRs vs SEA (WR Three's matchup) are
    # capped-tilt boosted; RB Three faces a neutral defense
    strength = {
        "positions": {
            "WR": {"SEA": {"multiplier": 1.2, "observed_ratio": 1.6,
                            "weeks_sampled": 3, "confidence": "medium",
                            "rank": 1}},
        },
    }
    result = run_optimize(engine, strength=strength)
    wr_three = next(
        s["player"]
        for s in result["optimal"]
        if s["player"] and s["player"]["player_name"] == "WR Three"
    )
    # 10.5 * 1.10 (alpha 0.5 * 20% = 10% tilt) = 11.55, enough to start
    assert wr_three["adjusted_projection"] == 11.55


def test_injury_and_bye_warnings_on_current_lineup():
    engine = make_engine()

    async def seed():
        await seed_league(engine)
        await seed_games(engine)  # no game for a team not in the slate
        entries = full_roster_entries()
        entries[1] = entry(2, "RB One", "RB", "SEA", "RB", 15.0, injury="out")
        entries[5] = entry(6, "TE One", "TE", "MIA", "TE", 9.0)  # MIA on bye
        await seed_roster(engine, entries)

    asyncio.run(seed())
    result = run_optimize(engine)
    assert any("RB One" in w and "out" in w for w in result["warnings"])
    assert any("TE One" in w and "bye" in w for w in result["warnings"])


def test_missing_roster_returns_none():
    engine = make_engine()
    asyncio.run(seed_league(engine))
    assert run_optimize(engine) is None


# --- the endpoint (cached-only path) ---------------------------------------------


def test_lineup_endpoint_serves_envelope_and_404s(client, app_module):
    engine = app_module.engine

    async def seed():
        await seed_league(engine)
        await seed_games(engine)
        await seed_roster(engine, full_roster_entries())

    asyncio.run(seed())
    payload = client.get(
        f"/inseason/league/{LEAGUE_ID}/lineup?espn_team_id=1"
    ).json()
    assert payload["data"]["delta_points"] == 2.5
    assert "freshness" in payload and "warnings" in payload
    assert (
        client.get(f"/inseason/league/{LEAGUE_ID}/lineup?espn_team_id=9").status_code
        == 404
    )
    assert (
        client.get("/inseason/league/999/lineup?espn_team_id=1").status_code == 404
    )


# --- the Thursday review notification ---------------------------------------------


def test_lineup_review_quotes_delta_for_the_mapped_team():
    engine = make_engine()

    async def go():
        await seed_league(engine)
        await seed_games(engine)
        await seed_roster(engine, full_roster_entries())
        first = await ensure_lineup_review(
            engine, LEAGUE_ID, SEASON, WEEK, my_teams={LEAGUE_ID: 1}
        )
        again = await ensure_lineup_review(
            engine, LEAGUE_ID, SEASON, WEEK, my_teams={LEAGUE_ID: 1}
        )
        return first, again

    first, again = asyncio.run(go())
    assert first.kind == "lineup_review"
    assert "+2.5" in first.body and "3 move(s)" in first.body
    assert again is None  # deduped: one review per league-week


def test_lineup_review_generic_when_team_unmapped():
    engine = make_engine()

    async def go():
        await seed_league(engine)
        return await ensure_lineup_review(
            engine, LEAGUE_ID, SEASON, WEEK, my_teams={}
        )

    notification = asyncio.run(go())
    assert "open the lineup optimizer" in notification.body


def test_lineup_review_none_for_unsynced_league():
    engine = make_engine()
    assert (
        asyncio.run(ensure_lineup_review(engine, 999, SEASON, WEEK, my_teams={}))
        is None
    )
