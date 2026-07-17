# -*- coding: utf-8 -*-
"""
E5: blocking plays — rivals' injured-star handcuffs worth grabbing purely
to deny. Pure-core tests build rosters/handcuffs/designations by hand
(test_handcuffs.py's mongomock + AIOEngine style) and cover the join, the
injury-status gate, the availability gate, the no-my-team skip, and the
two-sided E5/E6 boundary (an E5 case must NOT appear in E6's report).

The boundary is the load-bearing test here: E6 imports
rival_injured_star_handcuff_ids() from this module and subtracts it from
its candidate pool, so the same fixture is asserted from both sides.
"""
import asyncio
import datetime
import inspect

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.blocking import (
    BLOCKING_INJURY_STATUSES,
    blocking_plays,
    rival_injured_star_handcuff_ids,
)
from models.handcuffs import upsert_handcuff
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InjuryDesignation,
    InSeasonLeague,
    LeagueTeamInfo,
    RosterSlotEntry,
    TeamWeekRoster,
)

LEAGUE_ID = 222
SEASON = 2026
WEEK = 7


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-blocking")


# ESPN_MY_TEAMS is empty under conftest; patch the modules' imported copy
# so the user "owns" team 1 in the test league (makes rivals exist).
_MY_TEAMS = {LEAGUE_ID: 1}


def _patch_my_teams():
    import models.blocking as blk

    saved = blk.ESPN_MY_TEAMS
    blk.ESPN_MY_TEAMS = _MY_TEAMS
    return saved


def _restore_my_teams(saved):
    import models.blocking as blk

    blk.ESPN_MY_TEAMS = saved


class _MyTeams:
    def __enter__(self):
        self._saved = _patch_my_teams()
        return self

    def __exit__(self, *exc):
        _restore_my_teams(self._saved)


def _run(coro):
    # ESPN_MY_TEAMS must be patched for the duration of the async call.
    with _MyTeams():
        return asyncio.run(coro)


def _league(my_team_id=1, teams=None):
    if teams is None:
        teams = [
            LeagueTeamInfo(espn_team_id=1, name="My Team"),
            LeagueTeamInfo(espn_team_id=7, name="Rival A"),
            LeagueTeamInfo(espn_team_id=8, name="Rival B"),
        ]
    return InSeasonLeague(
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        name="Blocking League",
        team_count=len(teams),
        current_matchup_period=WEEK,
        latest_scoring_period=WEEK,
        final_scoring_period=17,
        lineup_slot_counts={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DST": 1},
        teams=teams,
    )


async def _seed(
    engine,
    starter_injury_status="out",
    starter_team_id=7,
    starter_name="Kenneth Walker III",
    handcuff_name="Zach Charbonnet",
    nfl_team="SEA",
    handcuff_available=True,
    designation=None,
):
    await engine.save(_league())
    # my team must be known for "rivals" to exist
    await engine.save(
        TeamWeekRoster(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=WEEK,
            espn_team_id=1,
            entries=[
                RosterSlotEntry(
                    player_id=500,
                    player_name="My QB",
                    position="QB",
                    lineup_slot="QB",
                    projected_points=15.0,
                )
            ],
        )
    )
    await engine.save(
        TeamWeekRoster(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=WEEK,
            espn_team_id=starter_team_id,
            entries=[
                RosterSlotEntry(
                    player_id=1,
                    player_name=starter_name,
                    position="RB",
                    nfl_team=nfl_team,
                    lineup_slot="RB",
                    injury_status=starter_injury_status,
                    projected_points=12.0,
                )
            ],
        )
    )
    entries = []
    if handcuff_available:
        entries.append(
            FreeAgentEntry(
                player_id=2,
                player_name=handcuff_name,
                position="RB",
                nfl_team=nfl_team,
                percent_owned=18.0,
                projected_points=9.0,
            )
        )
    if entries:
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID, season=SEASON, week=WEEK, entries=entries
            )
        )
    if designation is not None:
        await engine.save(
            InjuryDesignation(
                season=SEASON,
                week=WEEK,
                player_name=starter_name,
                nfl_team=nfl_team,
                position="RB",
                designation=designation,
            )
        )
    await upsert_handcuff(engine, starter_name, handcuff_name, nfl_team=nfl_team)


def test_blocking_modules_never_import_data_sources():
    """Structural purity: the cached-only read path can't even name data_sources."""
    import ast

    import models.blocking as blocking_mod
    import hoarding_api

    for module in (blocking_mod, hoarding_api):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for name in imported:
                assert not name.startswith("data_sources"), (
                    f"{module.__name__} imports {name}"
                )


def test_injured_rival_starter_handcuff_flagged():
    engine = make_engine()

    async def go():
        await _seed(engine, starter_injury_status="out")
        return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)

    report = _run(go())
    assert report["week"] == WEEK
    assert report["note"] is None
    (entry,) = report["entries"]
    assert entry["starter_name"] == "Kenneth Walker III"
    assert entry["handcuff_name"] == "Zach Charbonnet"
    assert entry["handcuff_player_id"] == 2
    assert entry["starter_team_id"] == 7  # a rival, not my team
    assert entry["starter_injury_status"] == "out"
    assert "denial" in entry["copy"].lower()
    # C8: copy speaks workload/insurance, never fantasy points
    assert "pts" not in entry["copy"].lower()


def test_status_gate_only_injured_starters_block():
    for status, expected in [
        ("out", True),
        ("doubtful", True),
        ("questionable", True),
        ("injury_reserve", True),
        ("healthy", False),
        (None, False),
    ]:
        engine = make_engine()

        async def go(status=status):
            await _seed(engine, starter_injury_status=status)
            return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)

        report = _run(go())
        if expected:
            assert len(report["entries"]) == 1, status
        else:
            assert report["entries"] == [], status


def test_d2_injury_designation_wins_over_espn_status():
    engine = make_engine()

    async def go():
        # ESPN says healthy, but D2 says out -> D2 wins (E1 §3.2 precedence)
        await _seed(
            engine,
            starter_injury_status=None,
            designation="out",
        )
        return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)

    report = _run(go())
    (entry,) = report["entries"]
    assert entry["starter_injury_status"] == "out"


def test_ir_designation_maps_into_blocking_set():
    engine = make_engine()

    async def go():
        await _seed(engine, starter_injury_status=None, designation="ir")
        return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)

    report = _run(go())
    assert len(report["entries"]) == 1
    assert "ir" in BLOCKING_INJURY_STATUSES


def test_no_flag_when_handcuff_not_in_free_agent_pool():
    engine = make_engine()

    async def go():
        await _seed(engine, starter_injury_status="out", handcuff_available=False)
        return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)

    report = _run(go())
    assert report["entries"] == []
    assert "no rival" in report["note"].lower()


def test_no_my_team_skips_league_with_note():
    engine = make_engine()

    async def go():
        await _seed(engine, starter_injury_status="out")
        # blow away ESPN_MY_TEAMS so there's no first-person perspective
        import models.blocking as blk

        saved = blk.ESPN_MY_TEAMS
        blk.ESPN_MY_TEAMS = {}
        try:
            return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)
        finally:
            blk.ESPN_MY_TEAMS = saved

    report = _run(go())
    assert report["entries"] == []
    assert "my-team" in report["note"].lower()


def test_unsynced_league_returns_note():
    engine = make_engine()
    report = _run(blocking_plays(engine, LEAGUE_ID, SEASON, WEEK))
    assert report["entries"] == []
    assert "not synced" in report["note"].lower()


def test_my_own_injured_starter_is_not_a_blocking_play():
    """A starter *I* roster being injured is my insurance problem (C7),
    not a denial target — blocking only flags RIVALS' injured starters."""
    engine = make_engine()

    async def go():
        # starter on MY team (team 1), injured — should NOT appear
        await _seed(engine, starter_injury_status="out", starter_team_id=1)
        return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)

    report = _run(go())
    assert report["entries"] == []


def test_exclusion_set_matches_report():
    """rival_injured_star_handcuff_ids (E6's exclusion input) returns
    exactly the handcuff ids that blocking_plays reports."""
    engine = make_engine()

    async def go():
        await _seed(engine, starter_injury_status="doubtful")
        report = await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)
        excluded = await rival_injured_star_handcuff_ids(
            engine, LEAGUE_ID, SEASON, WEEK
        )
        return report, excluded

    report, excluded = _run(go())
    assert excluded == {2}
    assert {e["handcuff_player_id"] for e in report["entries"]} == excluded


def test_two_rivals_each_with_injured_starter():
    engine = make_engine()

    async def go():
        await _seed(engine, starter_injury_status="out", starter_team_id=7)
        # second rival with a different injured starter
        await upsert_handcuff(engine, "Bijan Robinson", "Tyler Allgeier", nfl_team="ATL")
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=WEEK,
                espn_team_id=8,
                entries=[
                    RosterSlotEntry(
                        player_id=10,
                        player_name="Bijan Robinson",
                        position="RB",
                        nfl_team="ATL",
                        lineup_slot="RB",
                        injury_status="questionable",
                    )
                ],
            )
        )
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=WEEK,
                entries=[
                    FreeAgentEntry(
                        player_id=2,
                        player_name="Zach Charbonnet",
                        position="RB",
                        nfl_team="SEA",
                    ),
                    FreeAgentEntry(
                        player_id=11,
                        player_name="Tyler Allgeier",
                        position="RB",
                        nfl_team="ATL",
                    ),
                ],
            )
        )
        return await blocking_plays(engine, LEAGUE_ID, SEASON, WEEK)

    report = _run(go())
    names = {e["handcuff_name"] for e in report["entries"]}
    assert names == {"Zach Charbonnet", "Tyler Allgeier"}
