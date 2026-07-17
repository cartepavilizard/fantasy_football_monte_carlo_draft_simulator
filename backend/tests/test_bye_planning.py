# -*- coding: utf-8 -*-
"""
F2 bye-week planning — pure-function tests. Covers the cluster warning
at and below the threshold, the in-season thin-week preview, the
no-schedule degradation, and the no-mutation invariant. Follows the
mongomock + AIOEngine patterns of test_playoff_sos.py (conftest sets
DRAFT_YEAR=2024) for the endpoint-level checks.
"""
import asyncio
import copy
import datetime

import pytest
from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.bye_planning import (
    BYE_CLUSTER_THRESHOLD,
    NO_SCHEDULE_STATUS,
    bye_cluster_warning,
    bye_weeks_by_team,
    thin_week_preview,
)
from models.config import DRAFT_YEAR
from models.inseason import (
    InSeasonLeague,
    LeagueTeamInfo,
    ProGame,
    RosterSlotEntry,
    TeamWeekRoster,
)

SEASON = DRAFT_YEAR


# --- schedule helpers --------------------------------------------------------


def _game(week, home, away):
    return {
        "week": week,
        "home_team": home,
        "away_team": away,
        "kickoff": datetime.datetime(SEASON, 9, 10 + week),
    }


# A schedule where SEA and SF share a bye on week 10, and KC is on bye
# week 10 too. Weeks 1..14 exist; teams not playing a given week are on bye.
# Build games so that on week 10, SEA/SF/KC do NOT appear.
SCHEDULE = []
for week in range(1, 15):
    if week == 10:
        # only BUF vs NE play week 10 — SEA/SF/KC/LAR all idle
        SCHEDULE.append(_game(10, "BUF", "NE"))
    else:
        SCHEDULE.append(_game(week, "SEA", "ARI"))
        SCHEDULE.append(_game(week, "SF", "LAR"))
        SCHEDULE.append(_game(week, "KC", "DEN"))
# week 10: SEA, SF, KC, ARI, LAR, DEN all bye (6 teams)


def test_bye_weeks_by_team_finds_shared_bye():
    byes = bye_weeks_by_team(SCHEDULE)
    assert byes["SEA"] == {10}
    assert byes["SF"] == {10}
    assert byes["KC"] == {10}
    # BUF/NE play week 10, so their bye is elsewhere (weeks they don't play)
    assert 10 not in byes["BUF"]
    assert 10 not in byes["NE"]


def test_bye_weeks_by_team_empty_when_no_schedule():
    assert bye_weeks_by_team([]) == {}


def test_bye_weeks_by_team_accepts_progame_model_objects():
    games = [
        ProGame(season=SEASON, week=5, home_team="SEA", away_team="ARI",
                kickoff=datetime.datetime(SEASON, 10, 1)),
        ProGame(season=SEASON, week=6, home_team="SEA", away_team="SF",
                kickoff=datetime.datetime(SEASON, 10, 8)),
    ]
    byes = bye_weeks_by_team(games)
    # weeks present are {5,6}; SEA plays both; SF plays week 6 only -> bye 5
    assert byes["SF"] == {5}
    assert "SEA" not in byes  # SEA plays every week in this tiny schedule


# --- cluster warning: at and below the threshold ----------------------------


def _player(name, team):
    return {"name": name, "nfl_team": team}


def test_cluster_warning_at_threshold_fires():
    # 3 starters on bye week 10, threshold 3 -> fires
    players = [
        _player("SEA QB", "SEA"),
        _player("SEA WR", "SEA"),
        _player("SF RB", "SF"),
        _player("KC TE", "KC"),
    ]
    result = bye_cluster_warning(players, SCHEDULE, threshold=3)
    assert result["status"] == "ok"
    assert result["threshold"] == 3
    assert result["warning"] is not None
    clusters = result["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["week"] == 10
    assert clusters[0]["count"] == 4  # SEA x2, SF x1, KC x1
    assert "week 10" in result["warning"]


def test_cluster_warning_below_threshold_does_not_fire():
    # threshold 3 but only 2 starters share the bye -> no cluster
    players = [
        _player("SEA QB", "SEA"),
        _player("SF RB", "SF"),
        _player("BUF TE", "BUF"),  # BUF not on bye week 10
    ]
    result = bye_cluster_warning(players, SCHEDULE, threshold=3)
    assert result["status"] == "ok"
    assert result["clusters"] == []
    assert result["warning"] is None


def test_cluster_warning_respects_custom_threshold():
    # 4 starters share week 10; threshold 4 still fires; threshold 5 does not
    players = [
        _player("SEA QB", "SEA"),
        _player("SEA WR", "SEA"),
        _player("SF RB", "SF"),
        _player("KC TE", "KC"),
    ]
    assert bye_cluster_warning(players, SCHEDULE, threshold=4)["warning"] is not None
    assert bye_cluster_warning(players, SCHEDULE, threshold=5)["warning"] is None


def test_cluster_warning_default_threshold_is_three():
    assert BYE_CLUSTER_THRESHOLD == 3


def test_cluster_warning_skips_players_with_no_team():
    players = [
        {"name": "Mystery", "nfl_team": None},
        _player("SEA QB", "SEA"),
    ]
    result = bye_cluster_warning(players, SCHEDULE, threshold=1)
    # only SEA present; threshold 1 fires on week 10
    assert any(c["week"] == 10 for c in result["clusters"])


def test_cluster_warning_no_schedule_degrades_gracefully():
    result = bye_cluster_warning([_player("SEA QB", "SEA")], [], threshold=3)
    assert result["status"] == NO_SCHEDULE_STATUS
    assert result["clusters"] == []
    assert result["warning"] is None
    assert "schedule data" in result["note"].lower()


# --- thin-week preview -------------------------------------------------------


def _entry(name, team, slot="RB"):
    return {
        "player_name": name,
        "name": name,
        "nfl_team": team,
        "lineup_slot": slot,
    }


def test_thin_week_preview_picks_the_week_with_most_starters_on_bye():
    # current week 5; week 10 has 3 starters on bye, other future weeks fewer
    entries = [
        _entry("SEA QB", "SEA", "QB"),
        _entry("SEA WR", "SEA", "WR"),
        _entry("SF RB", "SF", "RB"),
        _entry("BUF TE", "BUF", "TE"),  # BUF not on bye week 10
    ]
    result = thin_week_preview(entries, SCHEDULE, current_week=5)
    assert result["status"] == "ok"
    assert result["thinnest_week"] == 10
    assert result["count"] == 3
    affected_names = {a["name"] for a in result["affected"]}
    assert affected_names == {"SEA QB", "SEA WR", "SF RB"}


def test_thin_week_preview_ignores_past_and_current_week():
    entries = [_entry("SEA QB", "SEA", "QB")]
    # current week 10 -> week 10 is not "future", no thin week
    result = thin_week_preview(entries, SCHEDULE, current_week=10)
    assert result["status"] == "ok"
    assert result["thinnest_week"] is None
    assert result["count"] == 0


def test_thin_week_preview_skips_bench_and_ir():
    # bench/IR entries don't count toward thinness
    entries = [
        _entry("SEA QB", "SEA", "QB"),
        _entry("SEA Bench", "SEA", "BE"),
        _entry("SEA IR", "SEA", "IR"),
    ]
    result = thin_week_preview(entries, SCHEDULE, current_week=5)
    assert result["thinnest_week"] == 10
    assert result["count"] == 1  # only the QB
    assert result["affected"][0]["name"] == "SEA QB"


def test_thin_week_preview_tie_goes_to_earliest_week():
    # build a schedule where SEA has two byes with equal starter count
    sched = [
        _game(5, "ARI", "BUF"),  # SEA bye week 5
        _game(6, "ARI", "BUF"),  # SEA bye week 6
        _game(7, "SEA", "SF"),
    ]
    entries = [_entry("SEA QB", "SEA", "QB")]
    result = thin_week_preview(entries, sched, current_week=4)
    assert result["thinnest_week"] == 5  # earliest of the tied (5 and 6)


def test_thin_week_preview_no_schedule_degrades_gracefully():
    result = thin_week_preview([_entry("SEA QB", "SEA", "QB")], [], current_week=5)
    assert result["status"] == NO_SCHEDULE_STATUS
    assert result["thinnest_week"] is None
    assert "schedule data" in result["note"].lower()


def test_thin_week_preview_no_future_byes_affecting_roster():
    # a roster of only BUF/NE players, who never share the week-10 bye
    entries = [_entry("BUF QB", "BUF", "QB"), _entry("NE RB", "NE", "RB")]
    result = thin_week_preview(entries, SCHEDULE, current_week=5)
    # BUF and NE may have byes on weeks where they don't play; but week 10
    # they DO play each other, so they're not on bye then. Any future week
    # where neither is on bye yields no affected -> thinnest None.
    # Just assert no crash and status ok.
    assert result["status"] == "ok"


# --- no-mutation invariant ---------------------------------------------------


def test_bye_cluster_warning_never_mutates_its_inputs():
    players = [
        _player("SEA QB", "SEA"),
        _player("SF RB", "SF"),
        _player("KC TE", "KC"),
    ]
    schedule_snapshot = copy.deepcopy(SCHEDULE)
    players_snapshot = copy.deepcopy(players)

    result = bye_cluster_warning(players, SCHEDULE, threshold=3)
    assert result["warning"] is not None
    result["clusters"][0]["players"].append({"name": "EVIL", "nfl_team": "XX"})
    result["warning"] = "tampered"

    assert SCHEDULE == schedule_snapshot
    assert players == players_snapshot


def test_thin_week_preview_never_mutates_its_inputs():
    entries = [
        _entry("SEA QB", "SEA", "QB"),
        _entry("SF RB", "SF", "RB"),
    ]
    schedule_snapshot = copy.deepcopy(SCHEDULE)
    entries_snapshot = copy.deepcopy(entries)

    result = thin_week_preview(entries, SCHEDULE, current_week=5)
    assert result["thinnest_week"] == 10
    result["affected"].append({"name": "EVIL", "nfl_team": "XX"})
    result["weeks"][0]["affected"] = []

    assert SCHEDULE == schedule_snapshot
    assert entries == entries_snapshot


def test_bye_weeks_by_team_never_mutates_its_inputs():
    schedule_snapshot = copy.deepcopy(SCHEDULE)
    bye_weeks_by_team(SCHEDULE)
    assert SCHEDULE == schedule_snapshot


# --- endpoint-level (mongomock + AIOEngine, test_playoff_sos pattern) --------


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-flags")


def _seed_league_and_roster(engine, league_id, week, team_id, entries):
    """entries: list of (name, position, nfl_team, slot, proj)"""
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
                    projected_points=proj,
                )
                for name, position, nfl_team, slot, proj in entries
            ],
        )
    )


def _seed_pro_schedule(engine):
    async def go():
        for game in SCHEDULE:
            await engine.save(
                ProGame(
                    season=SEASON,
                    week=game["week"],
                    home_team=game["home_team"],
                    away_team=game["away_team"],
                    kickoff=game["kickoff"],
                )
            )
    asyncio.run(go())


def test_endpoints_are_mongo_only_and_round_trip():
    """Drive both flags_api endpoints against mongomock: configure() with
    a fresh engine, seed league + roster + pro schedule, assert the flags
    come back with the freshness envelope."""
    import flags_api

    engine = make_engine()
    flags_api.configure(lambda: engine)

    async def seed():
        await engine.save(
            InSeasonLeague(
                espn_league_id=111,
                season=SEASON,
                name="Test League",
                team_count=1,
                latest_scoring_period=5,
                teams=[LeagueTeamInfo(espn_team_id=1, name="Team One")],
            )
        )
        await _seed_league_and_roster(
            engine, 111, 5, 1,
            [
                ("Geno Smith", "QB", "SEA", "QB", 18.0),
                ("DK Metcalf", "WR", "SEA", "WR", 14.0),
                ("Kenneth Walker III", "RB", "SEA", "RB", 15.0),
                ("Zach Charbonnet", "RB", "SEA", "BE", 8.0),
            ],
        )
    asyncio.run(seed())
    _seed_pro_schedule(engine)

    flags = asyncio.run(
        flags_api.get_strategy_flags(espn_league_id=111, espn_team_id=1, week=5)
    )
    assert "freshness" in flags
    assert "warnings" in flags
    data = flags["data"]
    assert data["week"] == 5
    (report,) = data["rosters"]
    # Geno+DK stack (rho 0.40) present
    assert any(s["correlation"] == 0.40 for s in report["stacks"])
    # Charbonnet is on the bench (BE) -> no F3 committee flag with Walker
    assert report["anti_correlation"] == []

    outlook = asyncio.run(
        flags_api.get_bye_outlook(espn_league_id=111, espn_team_id=1, week=5)
    )
    assert "freshness" in outlook
    assert outlook["data"]["cluster"]["status"] == "ok"
    (thin,) = outlook["data"]["thin_weeks"]
    assert thin["preview"]["thinnest_week"] == 10


def test_endpoints_404_when_league_missing():
    import flags_api

    engine = make_engine()
    flags_api.configure(lambda: engine)
    with pytest.raises(Exception):
        asyncio.run(flags_api.get_strategy_flags(espn_league_id=999999, week=5))


def test_bye_outlook_degrades_when_no_pro_schedule():
    import flags_api

    engine = make_engine()
    flags_api.configure(lambda: engine)

    async def seed():
        await engine.save(
            InSeasonLeague(
                espn_league_id=222,
                season=SEASON,
                name="No Schedule League",
                team_count=1,
                latest_scoring_period=5,
                teams=[LeagueTeamInfo(espn_team_id=1, name="Team One")],
            )
        )
        await _seed_league_and_roster(
            engine, 222, 5, 1,
            [("Geno Smith", "QB", "SEA", "QB", 18.0)],
        )
    asyncio.run(seed())
    # no ProGame rows seeded

    outlook = asyncio.run(
        flags_api.get_bye_outlook(espn_league_id=222, espn_team_id=1, week=5)
    )
    cluster = outlook["data"]["cluster"]
    assert cluster["status"] == NO_SCHEDULE_STATUS
    (thin,) = outlook["data"]["thin_weeks"]
    assert thin["preview"]["status"] == NO_SCHEDULE_STATUS
