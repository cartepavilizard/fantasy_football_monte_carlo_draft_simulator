# -*- coding: utf-8 -*-
"""
B4: the cached-only in-season read path. Everything here reads seeded
Mongo documents through the HTTP client; the two enforcement tests are
the point — the perspective switcher's hard constraint (no scrapes, no
external calls on any GET) is proven structurally and at runtime, not
assumed.
"""
import asyncio
import datetime
import inspect

from mongomock_motor import AsyncMongoMockClient

from models.config import DRAFT_YEAR, HOMER_TEAM
from models.handcuffs import upsert_handcuff
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueSyncLog,
    LeagueTeamInfo,
    LeagueTransaction,
    ProGame,
    RosterSlotEntry,
    TeamWeekRoster,
    TransactionItem,
    WeeklyMatchup,
)

SEASON = DRAFT_YEAR  # endpoints default to the configured season
LEAGUE_ID = 111
NOW = datetime.datetime.now()


def seed(app_module, sync_age_hours=1.0):
    """One synced league: two teams, week-5 data, fresh (or aged) sync logs"""
    engine = app_module.engine
    fetched_at = NOW - datetime.timedelta(hours=sync_age_hours)

    async def go():
        await engine.save(
            InSeasonLeague(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                name="The Family League",
                team_count=2,
                current_matchup_period=5,
                latest_scoring_period=5,
                final_scoring_period=17,
                trade_deadline=datetime.datetime(SEASON, 11, 18),
                lineup_slot_counts={"QB": 1, "RB": 2},
                teams=[
                    LeagueTeamInfo(espn_team_id=1, name="Emerald City Edge", wins=3),
                    LeagueTeamInfo(espn_team_id=2, name="Bro Squad", wins=1),
                ],
            )
        )
        for team_id, player in [(1, "Geno Smith"), (2, "Travis Kelce")]:
            await engine.save(
                TeamWeekRoster(
                    espn_league_id=LEAGUE_ID,
                    season=SEASON,
                    week=5,
                    espn_team_id=team_id,
                    entries=[
                        RosterSlotEntry(
                            player_id=team_id * 100,
                            player_name=player,
                            position="QB" if team_id == 1 else "TE",
                            lineup_slot="QB" if team_id == 1 else "FLEX",
                            projected_points=15.0,
                        )
                    ],
                )
            )
        await engine.save(
            WeeklyMatchup(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=5,
                home_team_id=1,
                away_team_id=2,
                home_points=101.0,
                away_points=88.0,
            )
        )
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=5,
                entries=[
                    FreeAgentEntry(
                        player_id=301,
                        player_name="Jaxon Smith-Njigba",
                        position="WR",
                        projected_points=14.1,
                    ),
                    FreeAgentEntry(
                        player_id=302,
                        player_name="Chiefs D/ST",
                        position="DST",
                        projected_points=7.0,
                    ),
                ],
            )
        )
        await engine.save(
            LeagueTransaction(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                espn_transaction_id="txn-1",
                type="WAIVER",
                status="EXECUTED",
                week=5,
                team_id=1,
                processed_at=NOW - datetime.timedelta(days=1),
                items=[
                    TransactionItem(
                        player_id=301,
                        player_name="Jaxon Smith-Njigba",
                        item_type="ADD",
                        to_team_id=1,
                    )
                ],
            )
        )
        # week 5 NFL slate: Thursday, Sunday, Monday
        for game_id, day, hour, minute, home, away in [
            (9004, 8, 20, 15, "SEA", "ARI"),  # Thursday night
            (9005, 11, 13, 0, "KC", "BUF"),
            (9006, 12, 20, 15, "DET", "GB"),  # Monday night
        ]:
            await engine.save(
                ProGame(
                    season=SEASON,
                    week=5,
                    espn_game_id=game_id,
                    home_team=home,
                    away_team=away,
                    kickoff=datetime.datetime(SEASON, 10, day, hour, minute),
                )
            )
        for section in ["league", "rosters", "matchups", "transactions", "free_agents"]:
            await engine.save(
                LeagueSyncLog(
                    espn_league_id=LEAGUE_ID,
                    season=SEASON,
                    section=section,
                    week=5,
                    success=True,
                    fetched_at=fetched_at,
                )
            )
        await engine.save(
            LeagueSyncLog(
                espn_league_id=None,
                season=SEASON,
                section="pro_schedule",
                success=True,
                fetched_at=fetched_at,
            )
        )

    asyncio.run(go())


# --- the hard constraint, enforced two ways -------------------------------------


def test_cached_only_modules_never_import_data_sources():
    """Structural enforcement: the read path (and everything it imports)
    cannot even name the code that talks to the outside world"""
    import ast

    import inseason_api
    import notifications_api
    from models import inseason as inseason_models
    from models import notifications as notification_models

    for module in (
        inseason_api,
        notifications_api,
        inseason_models,
        notification_models,
    ):
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
                    f"{module.__name__} imports {name} — the cached-only "
                    "read path must never reach a transport/adapter"
                )


def test_every_inseason_get_serves_with_the_network_rigged_to_explode(
    client, app_module, monkeypatch
):
    """Runtime enforcement: every GET succeeds while any HTTP-transport
    use would raise — switching perspective can never trigger a fetch"""
    from data_sources import transport as transport_module

    def boom(*args, **kwargs):
        raise AssertionError("a cached-only endpoint reached for the network")

    monkeypatch.setattr(transport_module.HttpxTransport, "_client_instance", boom)
    seed(app_module)
    for url in [
        "/inseason/overview",
        f"/inseason/league/{LEAGUE_ID}/roster?espn_team_id=1",
        f"/inseason/league/{LEAGUE_ID}/roster?espn_team_id=2",  # the switch
        f"/inseason/league/{LEAGUE_ID}/matchups",
        f"/inseason/league/{LEAGUE_ID}/transactions",
        f"/inseason/league/{LEAGUE_ID}/free_agents",
        f"/inseason/league/{LEAGUE_ID}/locks",
        f"/inseason/league/{LEAGUE_ID}/lineup?espn_team_id=1",
        f"/inseason/league/{LEAGUE_ID}/streaming",
        "/inseason/matchup_strength",
        "/inseason/playoff_sos",
        f"/inseason/playoff_sos?espn_league_id={LEAGUE_ID}",
        "/inseason/usage_shifts?week=5",
        "/inseason/handcuffs",
        f"/inseason/league/{LEAGUE_ID}/handcuffs",
        "/notifications",
        "/notifications/pending",
    ]:
        response = client.get(url)
        assert response.status_code == 200, f"{url}: {response.text}"


# --- reads ----------------------------------------------------------------------


def test_overview_lists_leagues_teams_and_freshness(client, app_module):
    seed(app_module)
    payload = client.get("/inseason/overview").json()
    assert payload["season"] == SEASON
    (entry,) = payload["leagues"]
    assert entry["league"]["name"] == "The Family League"
    team_names = [team["name"] for team in entry["league"]["teams"]]
    assert team_names == ["Emerald City Edge", "Bro Squad"]
    assert entry["freshness"]["stale"] is False
    assert entry["freshness"]["auth_expired"] is False
    assert entry["warnings"] == []


def test_roster_perspective_switch_serves_any_team(client, app_module):
    seed(app_module)
    mine = client.get(
        f"/inseason/league/{LEAGUE_ID}/roster?espn_team_id=1"
    ).json()
    theirs = client.get(
        f"/inseason/league/{LEAGUE_ID}/roster?espn_team_id=2"
    ).json()
    assert mine["data"]["entries"][0]["player_name"] == "Geno Smith"
    assert theirs["data"]["entries"][0]["player_name"] == "Travis Kelce"
    # week defaulted from the cached league doc, not a fetch
    assert mine["data"]["week"] == 5


def test_unknown_team_and_unsynced_league_404(client, app_module):
    seed(app_module)
    assert (
        client.get(f"/inseason/league/{LEAGUE_ID}/roster?espn_team_id=9").status_code
        == 404
    )
    response = client.get("/inseason/league/999/roster?espn_team_id=1")
    assert response.status_code == 404
    assert "sync" in response.json()["detail"]


def test_matchups_default_to_current_matchup_period(client, app_module):
    seed(app_module)
    payload = client.get(f"/inseason/league/{LEAGUE_ID}/matchups").json()
    assert payload["data"]["week"] == 5
    (matchup,) = payload["data"]["matchups"]
    assert (matchup["home_points"], matchup["away_points"]) == (101.0, 88.0)


def test_free_agents_filter_by_position(client, app_module):
    seed(app_module)
    payload = client.get(
        f"/inseason/league/{LEAGUE_ID}/free_agents?position=wr"
    ).json()
    agents = payload["data"]["free_agents"]
    assert [agent["player_name"] for agent in agents] == ["Jaxon Smith-Njigba"]


def test_transactions_read(client, app_module):
    seed(app_module)
    payload = client.get(f"/inseason/league/{LEAGUE_ID}/transactions").json()
    (transaction,) = payload["data"]
    assert transaction["espn_transaction_id"] == "txn-1"
    assert transaction["items"][0]["item_type"] == "ADD"


def test_streaming_ranks_kdst_free_agents(client, app_module):
    """C3: the free-agent pool filtered to K/DST, with matchup context.
    week 5's Chiefs D/ST has no nfl_team recorded in the seed, so its
    matchup is neutral (multiplier 1.0) — the endpoint still serves it."""
    seed(app_module)
    payload = client.get(f"/inseason/league/{LEAGUE_ID}/streaming").json()
    recs = payload["data"]["recommendations"]
    assert [r["player_name"] for r in recs] == ["Chiefs D/ST"]
    assert recs[0]["position"] == "DST"
    assert recs[0]["matchup"]["multiplier"] == 1.0
    assert recs[0]["rank"] == 1
    assert payload["data"]["week"] == 5


def test_league_handcuffs_flags_priority_and_homer_check(client, app_module):
    """C7: a rostered starter whose curated handcuff is a free agent
    surfaces as a flag; priority "high" when the starter is hurt, and a
    SEA (HOMER_TEAM) handcuff carries C9's neutral comparison."""
    seed(app_module)
    engine = app_module.engine

    async def add_handcuff_fixture():
        roster = await engine.find_one(
            TeamWeekRoster,
            (TeamWeekRoster.espn_league_id == LEAGUE_ID)
            & (TeamWeekRoster.season == SEASON)
            & (TeamWeekRoster.week == 5)
            & (TeamWeekRoster.espn_team_id == 1),
        )
        roster.entries.append(
            RosterSlotEntry(
                player_id=999,
                player_name="Kenneth Walker III",
                position="RB",
                nfl_team=HOMER_TEAM,
                lineup_slot="RB",
                injury_status="questionable",
                projected_points=12.0,
            )
        )
        await engine.save(roster)

        snapshot = await engine.find_one(
            FreeAgentSnapshot,
            (FreeAgentSnapshot.espn_league_id == LEAGUE_ID)
            & (FreeAgentSnapshot.season == SEASON)
            & (FreeAgentSnapshot.week == 5),
        )
        snapshot.entries.extend(
            [
                FreeAgentEntry(
                    player_id=998,
                    player_name="Zach Charbonnet",
                    position="RB",
                    nfl_team=HOMER_TEAM,
                    percent_owned=22.5,
                    projected_points=9.0,
                ),
                FreeAgentEntry(
                    player_id=997,
                    player_name="Some Other RB",
                    position="RB",
                    nfl_team="ATL",
                    percent_owned=5.0,
                    projected_points=4.0,
                ),
            ]
        )
        await engine.save(snapshot)

        await upsert_handcuff(
            engine, "Kenneth Walker III", "Zach Charbonnet", nfl_team=HOMER_TEAM
        )

    asyncio.run(add_handcuff_fixture())

    payload = client.get(f"/inseason/league/{LEAGUE_ID}/handcuffs").json()
    assert payload["data"]["week"] == 5
    (flag,) = payload["data"]["handcuffs"]
    assert flag["starter_name"] == "Kenneth Walker III"
    assert flag["handcuff_name"] == "Zach Charbonnet"
    assert flag["starter_team_id"] == 1
    assert flag["starter_injury_status"] == "questionable"
    assert flag["priority"] == "high"
    assert flag["handcuff_percent_owned"] == 22.5
    assert flag["homer_check"] is not None
    assert flag["homer_check"]["suggested"]["name"] == "Zach Charbonnet"


def test_locks_report_first_final_and_per_team(client, app_module):
    seed(app_module)
    payload = client.get(f"/inseason/league/{LEAGUE_ID}/locks").json()
    locks = payload["data"]["locks"]
    assert locks["first_game"] == "ARI @ SEA"
    assert locks["first_lock"] == f"{SEASON}-10-08T20:15:00"
    assert locks["final_lock"] == f"{SEASON}-10-12T20:15:00"
    assert locks["team_locks"]["KC"] == f"{SEASON}-10-11T13:00:00"


def test_stale_cache_is_served_with_a_visible_warning(client, app_module):
    """Degradation contract: old data still flows, loudly"""
    seed(app_module, sync_age_hours=72)  # way past INSEASON_STALE_AFTER_HOURS
    payload = client.get(
        f"/inseason/league/{LEAGUE_ID}/roster?espn_team_id=1"
    ).json()
    assert payload["data"]["entries"]  # cached data is still served
    assert payload["freshness"]["stale"] is True
    assert any("stale" in warning for warning in payload["warnings"])


def test_auth_failure_shows_up_in_every_read(client, app_module):
    seed(app_module)

    async def fail_auth():
        await app_module.engine.save(
            LeagueSyncLog(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                section="rosters",
                week=5,
                success=False,
                error="EspnAuthError: cookies expired",
                error_kind="auth",
                fetched_at=NOW,
            )
        )

    asyncio.run(fail_auth())
    payload = client.get(f"/inseason/league/{LEAGUE_ID}/matchups").json()
    assert payload["freshness"]["auth_expired"] is True
    assert any("cookies" in warning for warning in payload["warnings"])
