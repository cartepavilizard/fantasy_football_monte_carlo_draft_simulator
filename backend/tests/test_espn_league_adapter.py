# -*- coding: utf-8 -*-
"""
B1: the in-season ESPN league adapter. Payload fixtures mirror the real
lm-api-reads view shapes; no network. The sync tests exercise the
deliberate failure modes: cookie expiry degrades to cached data with
error_kind='auth' (never a crash, never stale-as-fresh), sections fail
independently, and re-syncs replace rather than duplicate.
"""
import asyncio
from datetime import datetime

import pytest
from conftest import FakeTransport, ScriptedTransport
from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from data_sources.base import SourceFetchError
from data_sources.espn_league import (
    EspnAuthError,
    EspnLeagueAdapter,
    sync_league,
    sync_pro_schedule,
)
from models.inseason import (
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueTransaction,
    ProGame,
    TeamWeekRoster,
    WeeklyMatchup,
    league_freshness,
)

SEASON, LEAGUE_ID, WEEK = 2026, 111, 5


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-inseason")


def find(engine, model):
    """asyncio.run needs a real coroutine; engine.find returns a cursor"""

    async def go():
        return await engine.find(model)

    return asyncio.run(go())


def make_adapter(transport, espn_s2="s2cookie", swid="{SWID-1}"):
    adapter = EspnLeagueAdapter(espn_s2=espn_s2, swid=swid, transport=transport)
    adapter._rate_limiter.min_interval_seconds = 0  # no pacing in tests
    return adapter


def ms(year, month, day, hour=13, minute=0):
    return int(datetime(year, month, day, hour, minute).timestamp() * 1000)


# --- payload fixtures (shapes verified against lm-api-reads responses) -------

LEAGUE_PAYLOAD = {
    "settings": {
        "name": "The Family League",
        "rosterSettings": {
            "lineupSlotCounts": {
                "0": 1, "2": 2, "4": 2, "6": 1, "16": 1, "17": 1,
                "20": 5, "21": 1, "23": 1, "3": 0,
            }
        },
        "tradeSettings": {"deadlineDate": ms(2026, 11, 18)},
    },
    "status": {
        "currentMatchupPeriod": 5,
        "latestScoringPeriod": 5,
        "finalScoringPeriod": 17,
    },
    "members": [
        {"id": "{GUID-1}", "firstName": "Carte", "lastName": "P"},
        {"id": "{GUID-2}", "displayName": "bro_in_law"},
    ],
    "teams": [
        {
            "id": 1,
            "name": "Emerald City Edge",
            "abbrev": "ECE",
            "owners": ["{GUID-1}"],
            "record": {
                "overall": {
                    "wins": 3, "losses": 1, "ties": 0,
                    "pointsFor": 512.5, "pointsAgainst": 480.1,
                }
            },
        },
        {
            "id": 2,
            "location": "Bro",
            "nickname": "Squad",
            "owners": ["{GUID-2}"],
            "record": {
                "overall": {
                    "wins": 1, "losses": 3, "ties": 0,
                    "pointsFor": 430.0, "pointsAgainst": 470.2,
                }
            },
        },
    ],
}


def make_player(pid, name, pos_id, pro_team_id, actual=None, projected=None,
                injury="ACTIVE"):
    stats = []
    if actual is not None:
        stats.append(
            {"scoringPeriodId": WEEK, "statSourceId": 0, "appliedTotal": actual}
        )
    if projected is not None:
        stats.append(
            {"scoringPeriodId": WEEK, "statSourceId": 1, "appliedTotal": projected}
        )
    return {
        "id": pid,
        "fullName": name,
        "defaultPositionId": pos_id,
        "proTeamId": pro_team_id,
        "injuryStatus": injury,
        "stats": stats,
    }


ROSTER_PAYLOAD = {
    "teams": [
        {
            "id": 1,
            "roster": {
                "entries": [
                    {
                        "lineupSlotId": 0,
                        "playerPoolEntry": {
                            "player": make_player(
                                101, "Geno Smith", 1, 26, actual=18.4, projected=17.2
                            )
                        },
                    },
                    {
                        "lineupSlotId": 20,
                        "playerPoolEntry": {
                            "player": make_player(
                                102, "Zach Charbonnet", 2, 26,
                                projected=11.0, injury="QUESTIONABLE",
                            )
                        },
                    },
                ]
            },
        },
        {
            "id": 2,
            "roster": {
                "entries": [
                    {
                        "lineupSlotId": 23,
                        "playerPoolEntry": {
                            "player": make_player(
                                201, "Travis Kelce", 4, 12, actual=9.9, projected=13.5
                            )
                        },
                    }
                ]
            },
        },
    ]
}

MATCHUP_PAYLOAD = {
    "schedule": [
        {
            "matchupPeriodId": 5,
            "home": {"teamId": 1, "totalPoints": 121.4},
            "away": {"teamId": 2, "totalPoints": 98.2},
            "winner": "HOME",
            "playoffTierType": "NONE",
        },
        {
            "matchupPeriodId": 15,
            "home": {"teamId": 2, "totalPoints": 0.0},
            "away": {"teamId": 1, "totalPoints": 0.0},
            "winner": "UNDECIDED",
            "playoffTierType": "WINNERS_BRACKET",
        },
        {  # bye matchup: no away side
            "matchupPeriodId": 6,
            "home": {"teamId": 1, "totalPoints": 0.0},
            "winner": "UNDECIDED",
        },
    ]
}

TRANSACTIONS_PAYLOAD = {
    "transactions": [
        {
            "id": "txn-1",
            "type": "WAIVER",
            "status": "EXECUTED",
            "scoringPeriodId": 5,
            "teamId": 1,
            "bidAmount": 12,
            "processDate": ms(2026, 10, 8, 9),
            "items": [
                {"playerId": 301, "type": "ADD", "toTeamId": 1},
                {"playerId": 102, "type": "DROP", "fromTeamId": 1},
            ],
        },
        {  # pure lineup shuffle: must be filtered out
            "id": "txn-2",
            "type": "ROSTER",
            "status": "EXECUTED",
            "scoringPeriodId": 5,
            "teamId": 2,
            "items": [{"playerId": 201, "type": "LINEUP"}],
        },
        {
            "id": "txn-3",
            "type": "TRADE_ACCEPT",
            "status": "EXECUTED",
            "scoringPeriodId": 4,
            "teamId": 2,
            "processDate": ms(2026, 10, 1, 12),
            "items": [
                {"playerId": 201, "type": "TRADE", "fromTeamId": 2, "toTeamId": 1},
                {"playerId": 101, "type": "TRADE", "fromTeamId": 1, "toTeamId": 2},
            ],
        },
    ]
}

FREE_AGENT_PAYLOAD = {
    "players": [
        {
            "player": {
                **make_player(301, "Jaxon Smith-Njigba", 3, 26, projected=14.1),
                "ownership": {"percentOwned": 88.5},
            }
        },
        {
            "player": {
                **make_player(302, "Chiefs D/ST", 16, 12, projected=7.0),
                "ownership": {"percentOwned": 45.0},
            }
        },
    ]
}

PRO_SCHEDULE_PAYLOAD = {
    "settings": {
        "proTeams": [
            {
                "id": 26,
                "proGamesByScoringPeriod": {
                    "1": [
                        {  # the Wednesday opener
                            "id": 9001,
                            "date": ms(2026, 9, 9, 19, 20),
                            "homeProTeamId": 12,
                            "awayProTeamId": 26,
                        }
                    ],
                    "5": [
                        {
                            "id": 9005,
                            "date": ms(2026, 10, 11, 13),
                            "homeProTeamId": 26,
                            "awayProTeamId": 22,
                        }
                    ],
                },
            },
            {
                "id": 12,
                "proGamesByScoringPeriod": {
                    "1": [
                        {  # duplicate of 9001, listed under the other team
                            "id": 9001,
                            "date": ms(2026, 9, 9, 19, 20),
                            "homeProTeamId": 12,
                            "awayProTeamId": 26,
                        }
                    ],
                    "5": [
                        {
                            "id": 9006,
                            "date": ms(2026, 10, 12, 20, 15),
                            "homeProTeamId": 12,
                            "awayProTeamId": 2,
                        }
                    ],
                },
            },
        ]
    }
}


def scripted_full_sync(overrides=None):
    """Responses in sync_league's fetch order; overrides swap one section"""
    responses = {
        "league": (200, LEAGUE_PAYLOAD),
        "rosters": (200, ROSTER_PAYLOAD),
        "matchups": (200, MATCHUP_PAYLOAD),
        "free_agents": (200, FREE_AGENT_PAYLOAD),
        "transactions": (200, TRANSACTIONS_PAYLOAD),
    }
    responses.update(overrides or {})
    return ScriptedTransport(
        [
            responses["league"],
            responses["rosters"],
            responses["matchups"],
            responses["free_agents"],
            responses["transactions"],
        ]
    )


# --- fetch/parse ---------------------------------------------------------------


def test_fetch_league_parses_settings_teams_and_deadline():
    transport = FakeTransport(payload=LEAGUE_PAYLOAD)
    adapter = make_adapter(transport)
    league = asyncio.run(adapter.fetch_league(LEAGUE_ID, SEASON))
    assert league.name == "The Family League"
    assert league.team_count == 2
    assert league.current_matchup_period == 5
    assert league.final_scoring_period == 17
    assert league.trade_deadline == datetime(2026, 11, 18, 13, 0)
    # zero-count slots dropped; ids translated to names
    assert league.lineup_slot_counts["RB"] == 2
    assert "RB/WR" not in league.lineup_slot_counts
    team_one, team_two = league.teams
    assert (team_one.name, team_one.owner_name) == ("Emerald City Edge", "Carte P")
    assert (team_two.name, team_two.owner_name) == ("Bro Squad", "bro_in_law")
    assert team_one.wins == 3 and team_one.points_for == 512.5
    # private-league auth rides on the Cookie header
    assert transport.last_headers["Cookie"] == "espn_s2=s2cookie; SWID={SWID-1}"


def test_missing_cookies_send_no_cookie_header():
    transport = FakeTransport(payload=LEAGUE_PAYLOAD)
    adapter = make_adapter(transport, espn_s2=None, swid=None)
    adapter.espn_s2 = None  # constructor falls back to env; force bare
    adapter.swid = None
    asyncio.run(adapter.fetch_league(LEAGUE_ID, SEASON))
    assert "Cookie" not in (transport.last_headers or {})


def test_401_raises_auth_error_not_generic_fetch_error():
    adapter = make_adapter(FakeTransport(status_code=401, payload={}))
    with pytest.raises(EspnAuthError, match="cookies"):
        asyncio.run(adapter.fetch_league(LEAGUE_ID, SEASON))


def test_fetch_rosters_parses_slots_points_and_injury():
    adapter = make_adapter(FakeTransport(payload=ROSTER_PAYLOAD))
    rosters = asyncio.run(adapter.fetch_rosters(LEAGUE_ID, SEASON, WEEK))
    assert [roster.espn_team_id for roster in rosters] == [1, 2]
    geno, charbonnet = rosters[0].entries
    assert (geno.lineup_slot, geno.position, geno.nfl_team) == ("QB", "QB", "SEA")
    assert (geno.actual_points, geno.projected_points) == (18.4, 17.2)
    assert charbonnet.lineup_slot == "BE"
    assert charbonnet.injury_status == "questionable"
    assert charbonnet.actual_points is None  # hasn't played yet
    assert rosters[1].entries[0].lineup_slot == "FLEX"


def test_fetch_matchups_handles_byes_and_playoffs():
    adapter = make_adapter(FakeTransport(payload=MATCHUP_PAYLOAD))
    matchups = asyncio.run(adapter.fetch_matchups(LEAGUE_ID, SEASON))
    week5, playoff, bye = matchups
    assert (week5.week, week5.winner, week5.home_points) == (5, "home", 121.4)
    assert playoff.is_playoff is True and playoff.winner is None
    assert bye.away_team_id is None


def test_fetch_transactions_filters_lineup_moves_and_resolves_names():
    adapter = make_adapter(FakeTransport(payload=TRANSACTIONS_PAYLOAD))
    transactions = asyncio.run(
        adapter.fetch_transactions(
            LEAGUE_ID, SEASON, WEEK,
            player_names={301: "Jaxon Smith-Njigba", 102: "Zach Charbonnet"},
        )
    )
    assert [t.espn_transaction_id for t in transactions] == ["txn-1", "txn-3"]
    waiver = transactions[0]
    assert waiver.bid_amount == 12
    add, drop = waiver.items
    assert (add.item_type, add.player_name) == ("ADD", "Jaxon Smith-Njigba")
    assert (drop.item_type, drop.player_name) == ("DROP", "Zach Charbonnet")
    trade = transactions[1]
    assert trade.type == "TRADE_ACCEPT"
    assert trade.items[0].player_name is None  # unknown ids stay unresolved


def test_fetch_free_agents_sends_filter_and_parses_pool():
    transport = FakeTransport(payload=FREE_AGENT_PAYLOAD)
    adapter = make_adapter(transport)
    snapshot = asyncio.run(adapter.fetch_free_agents(LEAGUE_ID, SEASON, WEEK, limit=50))
    assert "FREEAGENT" in transport.last_headers["X-Fantasy-Filter"]
    assert '"limit": 50' in transport.last_headers["X-Fantasy-Filter"]
    jsn, dst = snapshot.entries
    assert (jsn.position, jsn.percent_owned, jsn.projected_points) == (
        "WR", 88.5, 14.1,
    )
    assert dst.position == "DST"


def test_fetch_pro_schedule_dedupes_games_listed_under_both_teams():
    adapter = make_adapter(FakeTransport(payload=PRO_SCHEDULE_PAYLOAD))
    games = asyncio.run(adapter.fetch_pro_schedule(SEASON))
    assert len(games) == 3  # 9001 appears twice in the payload
    opener = next(game for game in games if game.espn_game_id == 9001)
    assert (opener.week, opener.home_team, opener.away_team) == (1, "KC", "SEA")
    assert opener.kickoff == datetime(2026, 9, 9, 19, 20)


# --- sync orchestration: failure modes are the feature --------------------------


def run_full_sync(engine, transport=None):
    adapter = make_adapter(transport or scripted_full_sync())
    return asyncio.run(
        sync_league(engine, LEAGUE_ID, season=SEASON, adapter=adapter)
    )


def test_full_sync_persists_every_section():
    engine = make_engine()
    summary = run_full_sync(engine)
    assert summary["week"] == 5  # taken from the league's latestScoringPeriod
    assert all(section["success"] for section in summary["sections"].values())

    async def counts():
        return (
            len(await engine.find(InSeasonLeague)),
            len(await engine.find(TeamWeekRoster)),
            len(await engine.find(WeeklyMatchup)),
            len(await engine.find(FreeAgentSnapshot)),
            len(await engine.find(LeagueTransaction)),
        )

    assert asyncio.run(counts()) == (1, 2, 3, 1, 2)
    # transaction names resolved from the same pass's rosters + free agents
    transactions = find(engine, LeagueTransaction)
    by_id = {t.espn_transaction_id: t for t in transactions}
    assert by_id["txn-1"].items[0].player_name == "Jaxon Smith-Njigba"
    freshness = asyncio.run(league_freshness(engine, LEAGUE_ID, SEASON))
    assert freshness["auth_expired"] is False
    for section in ["league", "rosters", "matchups", "free_agents", "transactions"]:
        assert freshness["sections"][section]["stale"] is False


def test_resync_replaces_instead_of_duplicating():
    engine = make_engine()
    run_full_sync(engine)
    run_full_sync(engine)
    assert len(find(engine, TeamWeekRoster)) == 2
    assert len(find(engine, WeeklyMatchup)) == 3
    assert len(find(engine, LeagueTransaction)) == 2
    assert len(find(engine, InSeasonLeague)) == 1


def test_cookie_expiry_degrades_to_cached_data_with_visible_warning():
    engine = make_engine()
    run_full_sync(engine)  # season in progress, cookies were good

    # cookies expire mid-season: every request now comes back 401
    expired = ScriptedTransport([(401, {"messages": ["Not authorized"]})] * 5)
    summary = run_full_sync(engine, transport=expired)

    for section in ["league", "rosters", "matchups", "free_agents", "transactions"]:
        assert summary["sections"][section]["success"] is False
        assert summary["sections"][section]["error_kind"] == "auth"
    # the cached data is untouched — degraded, not destroyed
    assert len(find(engine, TeamWeekRoster)) == 2
    assert len(find(engine, InSeasonLeague)) == 1
    # and the staleness surface says exactly what happened
    freshness = asyncio.run(league_freshness(engine, LEAGUE_ID, SEASON))
    assert freshness["auth_expired"] is True
    assert any("cookies appear expired" in warning for warning in freshness["warnings"])
    # the last GOOD sync is still reported, so the UI can show data age
    assert freshness["sections"]["rosters"]["last_success_at"] is not None


def test_sections_fail_independently():
    engine = make_engine()
    transport = scripted_full_sync(overrides={"rosters": (500, {})})
    summary = run_full_sync(engine, transport=transport)
    assert summary["sections"]["rosters"]["success"] is False
    assert summary["sections"]["rosters"]["error_kind"] == "http"
    assert summary["sections"]["league"]["success"] is True
    assert summary["sections"]["matchups"]["success"] is True
    assert summary["sections"]["free_agents"]["success"] is True
    # roster-dependent name resolution degrades: free-agent names still work
    transactions = find(engine, LeagueTransaction)
    by_id = {t.espn_transaction_id: t for t in transactions}
    assert by_id["txn-1"].items[0].player_name == "Jaxon Smith-Njigba"
    assert by_id["txn-1"].items[1].player_name is None  # was on the broken roster


def test_week_sections_skip_when_week_is_unknowable():
    engine = make_engine()  # nothing cached, and the settings view is down
    transport = ScriptedTransport([(500, {})])
    summary = run_full_sync(engine, transport=transport)
    assert summary["week"] is None
    for section in ["rosters", "transactions", "free_agents"]:
        assert summary["sections"][section]["error_kind"] == "skipped"


def test_week_sections_fall_back_to_cached_league_for_current_week():
    engine = make_engine()
    run_full_sync(engine)  # caches the league doc (week 5)
    # settings view fails this pass, but the rest proceed on cached week
    transport = scripted_full_sync(overrides={"league": (500, {})})
    # order matters: the failing league response is consumed first
    summary = run_full_sync(engine, transport=transport)
    assert summary["week"] == 5
    assert summary["sections"]["league"]["success"] is False
    assert summary["sections"]["rosters"]["success"] is True


def test_sync_pro_schedule_replaces_by_season():
    engine = make_engine()
    adapter = make_adapter(FakeTransport(payload=PRO_SCHEDULE_PAYLOAD))
    result = asyncio.run(sync_pro_schedule(engine, SEASON, adapter=adapter))
    assert result == {"success": True, "games": 3}
    result = asyncio.run(sync_pro_schedule(engine, SEASON, adapter=adapter))
    assert result["success"] is True
    assert len(find(engine, ProGame)) == 3  # replaced, not appended
