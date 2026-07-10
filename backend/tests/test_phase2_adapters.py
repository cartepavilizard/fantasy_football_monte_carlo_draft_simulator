# -*- coding: utf-8 -*-
"""
Phase 2 adapters: FantasyPros (API -> page-embed fallback), Yahoo
(OAuth refresh + idiosyncratic JSON), and UDK export parsing. Fixture
payloads mirror the real response shapes; no network involved.
"""
import asyncio
import json

import pytest
from conftest import FakeTransport, ScriptedTransport

from data_sources.base import SourceFetchError
from data_sources.fantasypros import FantasyProsAdapter, extract_ecr_data
from data_sources.udk import parse_udk_rows
from data_sources.yahoo import YahooAdapter

# --- FantasyPros -------------------------------------------------------------

FP_PLAYERS = {
    "players": [
        {
            "player_name": "Christian McCaffrey",
            "player_position_id": "RB",
            "player_team_id": "SF",
            "rank_ecr": 1,
            "pos_rank": "RB1",
            "tier": 1,
        },
        {
            "player_name": "Dallas Cowboys",
            "player_position_id": "DST",
            "player_team_id": "DAL",
            "rank_ecr": 130,
            "pos_rank": "DST5",
            "tier": 2,
        },
        {  # IDP row must be filtered out
            "player_name": "Some Linebacker",
            "player_position_id": "LB",
            "rank_ecr": 400,
        },
    ]
}

FP_PAGE_HTML = (
    "<html><head></head><body><script>var foo=1; var ecrData = "
    + json.dumps(FP_PLAYERS)
    + ";\nvar adpData = {};</script></body></html>"
)


def test_fantasypros_api_mode_parses_ranks_and_tiers():
    transport = FakeTransport(payload=FP_PLAYERS)
    adapter = FantasyProsAdapter(api_key="secret-key", transport=transport)
    records = asyncio.run(adapter.fetch(2024, "ppr"))
    assert transport.last_headers["x-api-key"] == "secret-key"
    assert transport.last_params["scoring"] == "PPR"
    assert "consensus-rankings" in transport.last_url
    cmc, dst = records
    assert (cmc.rank, cmc.position_rank, cmc.tier) == (1, 1.0, 1)
    assert (dst.raw_name, dst.position) == ("Dallas Cowboys", "DST")
    assert cmc.extra["access_mode"] == "api"


def test_fantasypros_page_mode_extracts_embedded_ecr_data():
    transport = FakeTransport(text=FP_PAGE_HTML)
    adapter = FantasyProsAdapter(api_key=None, transport=transport)
    records = asyncio.run(adapter.fetch(2024, "half_ppr"))
    assert "half-point-ppr-cheatsheets" in transport.last_url
    # Page path presents browser-like headers
    assert "Mozilla" in transport.last_headers["User-Agent"]
    assert len(records) == 2
    assert records[0].extra["access_mode"] == "page"


def test_fantasypros_falls_back_from_api_to_page():
    transport = ScriptedTransport([(403, {"error": "forbidden"}), (200, FP_PAGE_HTML)])
    adapter = FantasyProsAdapter(api_key="revoked-key", transport=transport)
    records = asyncio.run(adapter.fetch(2024, "ppr"))
    assert len(records) == 2
    assert records[0].extra["access_mode"] == "page"
    methods_and_urls = [(m, u) for m, u, _, _ in transport.requests]
    assert "api.fantasypros.com" in methods_and_urls[0][1]
    assert "cheatsheets" in methods_and_urls[1][1]


def test_ecr_extraction_fails_loudly_when_markup_changes():
    with pytest.raises(SourceFetchError, match="ecrData"):
        extract_ecr_data("<html>a cloudflare interstitial</html>")


# --- Yahoo --------------------------------------------------------------------

TOKEN_RESPONSE = {"access_token": "yat-123", "expires_in": 3600}

GAMES_RESPONSE = {
    "fantasy_content": {
        "games": {
            "0": {"game": [{"game_key": "449", "code": "nfl", "season": "2024"}]},
            "count": 1,
        }
    }
}


def yahoo_player(name, position, team, average_pick):
    return {
        "player": [
            [
                {"player_key": "449.p.1"},
                {"name": {"full": name, "first": name.split()[0]}},
                {"editorial_team_abbr": team},
                {"display_position": position},
            ],
            {
                "draft_analysis": [
                    {"average_pick": average_pick},
                    {"average_round": "1.1"},
                    {"percent_drafted": "0.99"},
                ]
            },
        ]
    }


PLAYERS_RESPONSE = {
    "fantasy_content": {
        "game": [
            {"game_key": "449"},
            {
                "players": {
                    "0": yahoo_player("Christian McCaffrey", "RB", "SF", "1.8"),
                    "1": yahoo_player("Philadelphia", "DEF", "Phi", "121.4"),
                    "2": yahoo_player("Undrafted Guy", "WR", "KC", "-"),
                    "count": 3,
                }
            },
        ]
    }
}


def make_yahoo_adapter(transport):
    return YahooAdapter(
        client_id="cid",
        client_secret="cs",
        refresh_token="rt",
        transport=transport,
    )


def test_yahoo_refreshes_token_then_pages_players():
    transport = ScriptedTransport(
        [
            (200, TOKEN_RESPONSE),  # POST token
            (200, GAMES_RESPONSE),  # GET game key for season
            (200, PLAYERS_RESPONSE),  # GET page 1 (3 < page size -> stop)
        ]
    )
    adapter = make_yahoo_adapter(transport)
    records = asyncio.run(adapter.fetch(2024, "ppr"))

    method, url, data, _ = transport.requests[0]
    assert (method, data["grant_type"]) == ("POST", "refresh_token")
    _, games_url, _, games_headers = transport.requests[1]
    assert "seasons=2024" in games_url
    assert games_headers["Authorization"] == "Bearer yat-123"
    _, players_url, _, _ = transport.requests[2]
    assert "game/449/players" in players_url and "draft_analysis" in players_url

    by_name = {record.raw_name: record for record in records}
    # "-" average_pick rows are unusable and dropped
    assert set(by_name) == {"Christian McCaffrey", "Philadelphia"}
    assert by_name["Christian McCaffrey"].adp == 1.8
    assert by_name["Philadelphia"].position == "DEF"
    assert by_name["Philadelphia"].nfl_team == "Phi"  # normalized later


def test_yahoo_without_credentials_fails_cleanly():
    adapter = YahooAdapter(
        client_id=None, client_secret=None, refresh_token=None, transport=object()
    )
    with pytest.raises(SourceFetchError, match="credentials not configured"):
        asyncio.run(adapter.fetch(2024, "ppr"))


def test_yahoo_token_rejection_fails_cleanly():
    transport = ScriptedTransport([(401, {"error": "invalid_grant"})])
    adapter = make_yahoo_adapter(transport)
    with pytest.raises(SourceFetchError, match="token refresh failed"):
        asyncio.run(adapter.fetch(2024, "ppr"))


# --- UDK export parsing --------------------------------------------------------


def test_udk_parses_typical_export_headers():
    rows = [
        {
            "Rank": "1",
            "Player": "Christian McCaffrey",
            "Pos": "RB",
            "Team": "SF",
            "Tier": "1",
            "Proj FFP": "320.5",
        },
        {
            "Rank": "130",
            "Player": "Dallas Cowboys",
            "Pos": "DST",
            "Team": "DAL",
            "Tier": "3",
            "Proj FFP": "115",
        },
    ]
    records, problems = parse_udk_rows(rows)
    assert problems == []
    cmc = records[0]
    assert (cmc.raw_name, cmc.position, cmc.rank, cmc.tier, cmc.projection) == (
        "Christian McCaffrey",
        "RB",
        1.0,
        1,
        320.5,
    )


def test_udk_maps_alternate_header_spellings():
    rows = [
        {
            "Overall Rank": "2",
            "Name": "CeeDee Lamb",
            "Position": "WR",
            "Tm": "DAL",
            "UDK Tier": "1",
            "Projected Points": "1,280.5",  # thousands separator tolerated
        }
    ]
    records, problems = parse_udk_rows(rows)
    assert problems == []
    assert records[0].raw_name == "CeeDee Lamb"
    assert records[0].projection == 1280.5


def test_udk_rejects_export_without_identity_or_value_columns():
    records, problems = parse_udk_rows([{"Foo": "1", "Bar": "2"}])
    assert records == []
    assert any("'name'" in problem for problem in problems)
    assert any("ranking value" in problem for problem in problems)
