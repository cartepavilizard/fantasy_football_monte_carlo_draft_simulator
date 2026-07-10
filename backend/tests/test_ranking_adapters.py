# -*- coding: utf-8 -*-
"""
Source adapters parse their API shapes into SourceRecords. Payloads are
fixtures mirroring each API's real response shape; no network involved.
"""
import asyncio
import json

import pytest
from conftest import FakeTransport

from data_sources.base import SourceFetchError
from data_sources.espn_rankings import EspnRankingsAdapter
from data_sources.ffc import FantasyFootballCalculatorAdapter
from data_sources.sleeper import SleeperAdapter

# --- Sleeper ----------------------------------------------------------------

SLEEPER_PAYLOAD = [
    {
        "player_id": "4034",
        "player": {
            "first_name": "Christian",
            "last_name": "McCaffrey",
            "position": "RB",
            "team": "SF",
        },
        "stats": {"adp_ppr": 1.5, "pts_ppr": 320.4, "adp_std": 2.0, "pts_std": 280.1},
    },
    {
        "player_id": "DAL",
        "player": {
            "first_name": "Dallas",
            "last_name": "Cowboys",
            "position": "DEF",
            "team": "DAL",
        },
        "stats": {"adp_ppr": 140.0, "pts_ppr": 110.0},
    },
    {  # Sleeper reports adp 0 for players nobody drafts
        "player_id": "8888",
        "player": {
            "first_name": "Zero",
            "last_name": "Adp",
            "position": "WR",
            "team": "KC",
        },
        "stats": {"adp_ppr": 0, "pts_ppr": 45.0},
    },
    {  # no adp AND no projection -> dropped
        "player_id": "9999",
        "player": {
            "first_name": "Practice",
            "last_name": "Squadman",
            "position": "RB",
            "team": None,
        },
        "stats": {},
    },
]


def test_sleeper_parses_players_and_defenses():
    adapter = SleeperAdapter(transport=FakeTransport(payload=SLEEPER_PAYLOAD))
    records = asyncio.run(adapter.fetch(2024, "ppr"))
    by_name = {record.raw_name: record for record in records}
    assert set(by_name) == {"Christian McCaffrey", "Dallas Cowboys", "Zero Adp"}
    cmc = by_name["Christian McCaffrey"]
    assert (cmc.position, cmc.nfl_team, cmc.adp, cmc.projection) == (
        "RB",
        "SF",
        1.5,
        320.4,
    )
    assert by_name["Zero Adp"].adp is None  # 0 means undrafted, not pick 0
    assert by_name["Zero Adp"].projection == 45.0


def test_sleeper_uses_format_specific_stat_keys():
    transport = FakeTransport(payload=SLEEPER_PAYLOAD)
    adapter = SleeperAdapter(transport=transport)
    records = asyncio.run(adapter.fetch(2024, "standard"))
    cmc = next(r for r in records if r.raw_name == "Christian McCaffrey")
    assert (cmc.adp, cmc.projection) == (2.0, 280.1)
    assert transport.last_params["order_by"] == "adp_std"


def test_sleeper_rejects_unknown_format():
    adapter = SleeperAdapter(transport=FakeTransport(payload=SLEEPER_PAYLOAD))
    with pytest.raises(SourceFetchError, match="scoring format"):
        asyncio.run(adapter.fetch(2024, "superflex"))


# --- FantasyFootballCalculator ----------------------------------------------

FFC_PAYLOAD = {
    "status": "Success",
    "meta": {"type": "PPR"},
    "players": [
        {
            "player_id": 1,
            "name": "Christian McCaffrey",
            "position": "RB",
            "team": "SF",
            "adp": 1.3,
            "times_drafted": 300,
            "stdev": 0.7,
        },
        {"player_id": 2, "name": "Justin Tucker", "position": "PK", "team": "BAL", "adp": 130.2},
        {"player_id": 3, "name": "San Francisco Defense", "position": "DEF", "team": "SF", "adp": 120.0},
    ],
}


def test_ffc_parses_adp_and_passes_year_param():
    transport = FakeTransport(payload=FFC_PAYLOAD)
    adapter = FantasyFootballCalculatorAdapter(teams=10, transport=transport)
    records = asyncio.run(adapter.fetch(2019, "half_ppr"))
    assert transport.last_url.endswith("/adp/half-ppr")
    # year is how Phase 3 backfills historical ADP for reach features
    assert transport.last_params == {"teams": 10, "year": 2019, "position": "all"}
    assert [record.raw_name for record in records] == [
        "Christian McCaffrey",
        "Justin Tucker",
        "San Francisco Defense",
    ]
    assert records[0].projection is None  # FFC is ADP-only
    assert records[1].position == "PK"  # raw kept; blend normalizes to k
    assert records[0].extra["times_drafted"] == 300


def test_ffc_error_status_raises():
    payload = {"status": "Error", "players": []}
    adapter = FantasyFootballCalculatorAdapter(transport=FakeTransport(payload=payload))
    with pytest.raises(SourceFetchError, match="Error"):
        asyncio.run(adapter.fetch(2024, "ppr"))


# --- ESPN rankings/ADP --------------------------------------------------------

ESPN_PAYLOAD = {
    "players": [
        {
            "player": {
                "id": 3117251,
                "fullName": "Christian McCaffrey",
                "defaultPositionId": 2,  # RB
                "proTeamId": 25,  # SF
                "ownership": {"averageDraftPosition": 1.8, "percentOwned": 99.9},
                "draftRanksByRankType": {
                    "PPR": {"rank": 1},
                    "STANDARD": {"rank": 2},
                },
                "stats": [
                    {
                        "seasonId": 2024,
                        "statSourceId": 1,  # projection
                        "statSplitTypeId": 0,  # full season
                        "appliedTotal": 315.2,
                    },
                    {
                        "seasonId": 2024,
                        "statSourceId": 0,  # actuals must not be mistaken
                        "statSplitTypeId": 0,
                        "appliedTotal": 12.0,
                    },
                ],
            }
        },
        {
            "player": {
                "id": 4,
                "fullName": "Cowboys D/ST",
                "defaultPositionId": 16,  # DST
                "proTeamId": 6,  # DAL
                "ownership": {"averageDraftPosition": 130.5},
                "draftRanksByRankType": {"PPR": {"rank": 120}},
                "stats": [],
            }
        },
        {
            "player": {  # non-fantasy position id -> filtered out
                "id": 5,
                "fullName": "Some Longsnapper",
                "defaultPositionId": 9,
                "proTeamId": 6,
            }
        },
    ]
}


def test_espn_maps_ids_and_extracts_projection():
    transport = FakeTransport(payload=ESPN_PAYLOAD)
    adapter = EspnRankingsAdapter(transport=transport)
    records = asyncio.run(adapter.fetch(2024, "ppr"))
    assert [record.raw_name for record in records] == [
        "Christian McCaffrey",
        "Cowboys D/ST",
    ]
    cmc, dst = records
    assert (cmc.position, cmc.nfl_team) == ("RB", "SF")
    assert cmc.adp == 1.8
    assert cmc.rank == 1  # the PPR rank, since format is ppr
    assert cmc.projection == 315.2  # statSourceId=1 only
    assert (dst.position, dst.nfl_team, dst.rank) == ("DST", "DAL", 120)
    # The kona view is filtered/sorted via the X-Fantasy-Filter header
    fantasy_filter = json.loads(transport.last_headers["X-Fantasy-Filter"])
    assert fantasy_filter["players"]["sortDraftRanks"]["value"] == "PPR"
    assert transport.last_params == {"view": "kona_player_info"}


def test_espn_standard_format_uses_standard_ranks():
    adapter = EspnRankingsAdapter(transport=FakeTransport(payload=ESPN_PAYLOAD))
    records = asyncio.run(adapter.fetch(2024, "standard"))
    cmc = records[0]
    assert cmc.rank == 2  # STANDARD rank


def test_espn_empty_response_raises():
    adapter = EspnRankingsAdapter(transport=FakeTransport(payload={"players": []}))
    with pytest.raises(SourceFetchError, match="no usable players"):
        asyncio.run(adapter.fetch(2024, "ppr"))
