# -*- coding: utf-8 -*-
"""
ESPN history ingestion + owner endpoints, end to end against stub
espn-api leagues and a faked FFC (no network): normalization, ADP and
position backfill, keeper/auction handling, idempotent re-ingest,
per-season logging, alias merging, and profile retrieval.
"""
import asyncio

import pytest
from conftest import FakeTransport
from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from data_sources.espn_history import EspnHistoryIngester, ingest_league_history
from data_sources.ffc import FantasyFootballCalculatorAdapter
from models.sources import HistoricalIngestLog, HistoricalPick


# --- espn-api stand-ins -------------------------------------------------------


class StubTeam:
    def __init__(self, team_id, guid, first, last):
        self.team_id = team_id
        self.owners = [{"id": guid, "firstName": first, "lastName": last}]


class StubPick:
    def __init__(self, team, name, round_num, round_pick, keeper=False, bid=0):
        self.team = team
        self.playerName = name
        self.round_num = round_num
        self.round_pick = round_pick
        self.keeper_status = keeper
        self.bid_amount = bid


class StubLeague:
    def __init__(self, teams, draft, previous_seasons=()):
        self.teams = teams
        self.draft = draft
        self.previousSeasons = list(previous_seasons)


DAVE = StubTeam(1, "{G-DAVE}", "Dave", "K")
SARAH = StubTeam(2, "{G-SARAH}", "Sarah", "M")

LEAGUE_2024 = StubLeague(
    teams=[DAVE, SARAH],
    draft=[
        StubPick(DAVE, "Christian McCaffrey", 1, 1),
        StubPick(SARAH, "CeeDee Lamb", 1, 2),
        StubPick(SARAH, "Josh Allen", 2, 1),
        StubPick(DAVE, "Cowboys D/ST", 2, 2),
        StubPick(DAVE, "Sneaky Keeper", 3, 1, keeper=True),
        StubPick(SARAH, "Unknown Rookie", 3, 2),  # no FFC match
    ],
    previous_seasons=[2023],
)

LEAGUE_2023 = StubLeague(
    teams=[DAVE, SARAH],
    draft=[
        StubPick(DAVE, "CeeDee Lamb", 1, 1),
        StubPick(SARAH, "Christian McCaffrey", 1, 2),
    ],
)

STUB_LEAGUES = {(111, 2024): LEAGUE_2024, (111, 2023): LEAGUE_2023}

FFC_HISTORY = {
    "status": "Success",
    "players": [
        {"name": "Christian McCaffrey", "position": "RB", "team": "SF", "adp": 2.0},
        {"name": "CeeDee Lamb", "position": "WR", "team": "DAL", "adp": 3.1},
        {"name": "Josh Allen", "position": "QB", "team": "BUF", "adp": 20.0},
        {"name": "Dallas Defense", "position": "DEF", "team": "DAL", "adp": 100.0},
    ],
}


def stub_factory(espn_league_id, season, espn_s2=None, swid=None):
    try:
        return STUB_LEAGUES[(espn_league_id, season)]
    except KeyError:
        raise RuntimeError(f"ESPN says 404 for {espn_league_id}/{season}")


def make_ingester():
    return EspnHistoryIngester(
        espn_s2="s2",
        swid="{swid}",
        league_factory=stub_factory,
        ffc_adapter=FantasyFootballCalculatorAdapter(
            transport=FakeTransport(payload=FFC_HISTORY)
        ),
    )


@pytest.fixture()
def stub_espn(monkeypatch):
    """Route the app's real ingest path through the stubs"""
    from data_sources import espn_history

    monkeypatch.setattr(espn_history, "create_espn_league", stub_factory)
    monkeypatch.setattr(
        espn_history,
        "FantasyFootballCalculatorAdapter",
        lambda **kwargs: FantasyFootballCalculatorAdapter(
            transport=FakeTransport(payload=FFC_HISTORY)
        ),
    )


# --- ingester unit behavior ----------------------------------------------------


def fresh_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-history")


def test_ingest_normalizes_enriches_and_logs():
    engine = fresh_engine()
    summary = asyncio.run(
        ingest_league_history(engine, 111, ingester=make_ingester())
    )
    assert summary["ingested_seasons"] == 2  # discovered 2023 via previousSeasons
    assert summary["seasons"][2024]["picks"] == 6

    async def load():
        picks = await engine.find(HistoricalPick, HistoricalPick.season == 2024)
        logs = await engine.find(HistoricalIngestLog)
        return list(picks), list(logs)

    picks, logs = asyncio.run(load())
    by_name = {pick.raw_player_name: pick for pick in picks}

    cmc = by_name["Christian McCaffrey"]
    assert (cmc.overall_pick, cmc.round_num, cmc.round_pick) == (1, 1, 1)
    assert cmc.member_guid == "{G-DAVE}"
    assert cmc.owner_display_name == "Dave K"
    assert (cmc.position, cmc.historical_adp) == ("rb", 2.0)

    josh = by_name["Josh Allen"]
    assert josh.overall_pick == 3  # round 2 pick 1 in a 2-team draft
    assert josh.member_guid == "{G-SARAH}"

    dst = by_name["Cowboys D/ST"]
    assert (dst.position, dst.historical_adp) == ("dst", 100.0)  # via team table

    assert by_name["Sneaky Keeper"].is_keeper is True
    unknown = by_name["Unknown Rookie"]
    assert unknown.position is None and unknown.historical_adp is None

    log_2024 = next(log for log in logs if log.season == 2024)
    assert (log_2024.picks, log_2024.keepers, log_2024.auction) == (6, 1, False)
    assert log_2024.position_matched == 4
    assert log_2024.adp_matched == 4


def test_reingest_replaces_instead_of_duplicating():
    engine = fresh_engine()
    asyncio.run(ingest_league_history(engine, 111, ingester=make_ingester()))
    asyncio.run(ingest_league_history(engine, 111, ingester=make_ingester()))

    async def count():
        return len(await engine.find(HistoricalPick))

    assert asyncio.run(count()) == 8  # 6 (2024) + 2 (2023), no duplicates


def test_failed_season_is_logged_not_fatal():
    engine = fresh_engine()
    summary = asyncio.run(
        ingest_league_history(
            engine, 111, seasons=[2024, 1999], ingester=make_ingester()
        )
    )
    assert summary["ingested_seasons"] == 1
    assert summary["failed_seasons"] == 1
    assert "404" in summary["seasons"][1999]["error"]

    async def failed_log():
        logs = await engine.find(HistoricalIngestLog)
        return next(log for log in logs if log.season == 1999)

    log = asyncio.run(failed_log())
    assert log.success is False and "404" in log.error


# --- endpoints -------------------------------------------------------------------


def test_ingest_endpoint_builds_profiles(client, stub_espn):
    response = client.post("/owners/ingest/111")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["ingested_seasons"] == 2
    assert summary["profiles"]["profiles"] == 2

    owners = client.get("/owners").json()
    assert {owner["profile_key"] for owner in owners} == {"{G-DAVE}", "{G-SARAH}"}
    dave = next(o for o in owners if o["profile_key"] == "{G-DAVE}")
    assert dave["display_names"] == ["Dave K"]
    assert dave["seasons"] == [2023, 2024]
    assert dave["has_profile"] is True

    profile = client.get("/owners/{G-DAVE}/profile").json()
    assert profile["total_picks_observed"] == 3  # keeper pick excluded
    reach = profile["metrics"]["reach"]
    assert reach["n"] == 3
    bucket = profile["metrics"]["position_frequency"]["1-2"]
    assert bucket["n"] == 3
    assert set(bucket["shares"]) == {"rb", "wr", "dst"}


def test_unreachable_league_is_502(client, stub_espn):
    response = client.post("/owners/ingest/999")
    assert response.status_code == 502
    assert "999" in response.json()["detail"]


def test_alias_merges_owners_and_rebuilds(client, stub_espn):
    client.post("/owners/ingest/111")
    response = client.post(
        "/owners/alias?member_guid={G-SARAH}&profile_key={G-DAVE}"
        "&note=same person, second account"
    )
    assert response.status_code == 200
    owners = client.get("/owners").json()
    assert len(owners) == 1
    merged = owners[0]
    assert set(merged["member_guids"]) == {"{G-DAVE}", "{G-SARAH}"}
    profile = client.get("/owners/{G-DAVE}/profile").json()
    assert sorted(profile["member_guids"]) == ["{G-DAVE}", "{G-SARAH}"]
    assert profile["total_picks_observed"] == 7  # everything but the keeper


def test_profile_404_for_unknown_owner(client):
    assert client.get("/owners/nobody/profile").status_code == 404
