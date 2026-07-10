# -*- coding: utf-8 -*-
"""
End-to-end Phase 1 flow through the API: refresh (with stubbed source
adapters) -> blended rankings -> sync into a league's players, with no
CSV touched for the player pool. Also covers cross-source name
resolution against the ESPN anchor namespace.
"""
import pytest
from conftest import sample, upload

from data_sources.base import BaseSourceAdapter, SourceFetchError, SourceRecord


class StubAdapter(BaseSourceAdapter):
    """Returns canned records; raises if constructed with an error"""

    source_name = "stub"
    min_request_interval_seconds = 0.0

    def __init__(self, name, records=None, error=None):
        super().__init__(transport=object())  # transport never used
        self.source_name = name
        self._records = records or []
        self._error = error

    async def fetch(self, season, scoring_format):
        if self._error:
            raise SourceFetchError(self._error)
        return self._records


def rec(name, position, **fields):
    return SourceRecord(raw_name=name, position=position, **fields)


# ESPN is the anchor: its spellings become canonical
ESPN_RECORDS = [
    rec("Patrick Mahomes", "QB", nfl_team="KC", projection=380.0, adp=25.0, rank=24),
    rec("Josh Allen", "QB", nfl_team="BUF", projection=390.0, adp=20.0, rank=19),
    rec("Christian McCaffrey", "RB", nfl_team="SF", projection=320.0, adp=1.5, rank=1),
    rec("Bijan Robinson", "RB", nfl_team="ATL", projection=290.0, adp=4.0, rank=4),
    rec("A.J. Brown", "WR", nfl_team="PHI", projection=260.0, adp=9.0, rank=9),
    rec("CeeDee Lamb", "WR", nfl_team="DAL", projection=280.0, adp=3.0, rank=3),
    rec("Travis Kelce", "TE", nfl_team="KC", projection=200.0, adp=15.0, rank=14),
    rec("Sam LaPorta", "TE", nfl_team="DET", projection=190.0, adp=30.0, rank=29),
    rec("Cowboys D/ST", "DST", nfl_team="DAL", projection=120.0, adp=110.0, rank=105),
    rec("49ers D/ST", "DST", nfl_team="SF", projection=115.0, adp=120.0, rank=115),
    rec("Justin Tucker", "K", nfl_team="BAL", projection=140.0, adp=130.0, rank=125),
    rec("Harrison Butker", "K", nfl_team="KC", projection=138.0, adp=135.0, rank=130),
]

# Sleeper spells some players differently — must resolve to ESPN spellings
SLEEPER_RECORDS = [
    rec("Patrick Mahomes", "QB", nfl_team="KC", projection=376.0, adp=26.0),
    rec("Josh Allen", "QB", nfl_team="BUF", projection=395.0, adp=18.0),
    rec("Christian McCaffrey", "RB", nfl_team="SF", projection=330.0, adp=1.2),
    rec("Bijan Robinson", "RB", nfl_team="ATL", projection=285.0, adp=5.0),
    rec("AJ Brown", "WR", nfl_team="PHI", projection=255.0, adp=10.0),  # no periods
    rec("CeeDee Lamb", "WR", nfl_team="DAL", projection=286.0, adp=2.8),
    rec("Dallas Cowboys", "DEF", nfl_team="DAL", projection=118.0, adp=112.0),
]

# FFC: ADP only, kicker as PK, defense as "<City> Defense"
FFC_RECORDS = [
    rec("Patrick Mahomes", "QB", adp=27.0),
    rec("Josh Allen", "QB", adp=19.0),
    rec("Christian McCaffrey", "RB", adp=1.8),
    rec("Bijan Robinson", "RB", adp=4.5),
    rec("Justin Tucker", "PK", nfl_team="BAL", adp=128.0),
    rec("Harrison Butker", "PK", nfl_team="KC", adp=133.0),
    rec("San Francisco Defense", "DEF", nfl_team="SF", adp=121.0),
]


@pytest.fixture()
def stub_sources(monkeypatch):
    """Replace real adapter construction with canned-data stubs"""
    from data_sources import service

    def fake_build_adapters(sources=None):
        available = {
            "espn": lambda: StubAdapter("espn", ESPN_RECORDS),
            "sleeper": lambda: StubAdapter("sleeper", SLEEPER_RECORDS),
            "ffc": lambda: StubAdapter("ffc", FFC_RECORDS),
        }
        names = sources or list(available)
        unknown = sorted(set(names) - set(available))
        if unknown:
            raise ValueError(f"Unknown ranking sources {unknown}")
        return {name: available[name]() for name in names}

    monkeypatch.setattr(service, "build_adapters", fake_build_adapters)


def test_refresh_fetches_resolves_and_blends(client, stub_sources):
    response = client.post("/rankings/refresh")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["season"] == 2024  # conftest pins DRAFT_YEAR
    for source in ["espn", "sleeper", "ffc"]:
        assert summary["sources"][source]["success"] is True
        assert summary["sources"][source]["unresolved"] == 0
    assert summary["blend"]["records"] == len(ESPN_RECORDS)
    assert set(summary["blend"]["sources_used"]) == {"espn", "sleeper", "ffc"}


def test_blended_rankings_use_espn_spellings_as_canonical(client, stub_sources):
    client.post("/rankings/refresh")
    blend = client.get("/rankings/blended").json()
    names = {record["canonical_name"] for record in blend["records"]}
    # Sleeper's "AJ Brown" and FFC's "San Francisco Defense" resolved to
    # the ESPN anchor spellings
    assert "A.J. Brown" in names
    assert "AJ Brown" not in names
    assert "49ers D/ST" in names
    aj = next(r for r in blend["records"] if r["canonical_name"] == "A.J. Brown")
    assert set(aj["source_values"]) == {"espn", "sleeper"}
    assert aj["blended_projection"] == pytest.approx(257.5)  # mean(260, 255)


def test_blended_rankings_404_before_any_refresh(client):
    assert client.get("/rankings/blended").status_code == 404


def test_refresh_with_unknown_source_is_400(client, stub_sources):
    response = client.post("/rankings/refresh?sources=espn,udk")
    assert response.status_code == 400
    assert "udk" in response.json()["detail"]


def test_one_broken_source_degrades_not_breaks(client, stub_sources, monkeypatch):
    from data_sources import service

    def build_with_failure(sources=None):
        return {
            "espn": StubAdapter("espn", ESPN_RECORDS),
            "sleeper": StubAdapter("sleeper", error="sleeper exploded"),
        }

    monkeypatch.setattr(service, "build_adapters", build_with_failure)
    summary = client.post("/rankings/refresh").json()
    assert summary["sources"]["sleeper"]["success"] is False
    assert "sleeper exploded" in summary["sources"]["sleeper"]["error"]
    assert summary["sources"]["espn"]["success"] is True
    assert summary["blend"]["sources_used"] == ["espn"]
    assert summary["blend"]["records"] > 0


def test_sync_requires_a_blend_first(client, league_id):
    response = client.post(f"/league/{league_id}/player/sync")
    assert response.status_code == 404
    assert "refresh" in response.json()["detail"]


def test_sync_materializes_blend_into_league_players(client, stub_sources, league_id):
    client.post("/rankings/refresh")
    response = client.post(f"/league/{league_id}/player/sync")
    assert response.status_code == 200, response.text
    league = response.json()
    names = {player["name"] for player in league["players"]["players"]}
    assert names == {record.raw_name for record in ESPN_RECORDS}
    assert league["ready_position_max_points"] is True
    # Consensus fields rode along; positions were grouped and tiered
    cmc = next(
        p for p in league["players"]["rb"] if p["name"] == "Christian McCaffrey"
    )
    assert cmc["adp"] == pytest.approx((1.5 + 1.2 + 1.8) / 3, abs=0.01)
    assert cmc["source_values"]  # per-source z-scores preserved
    assert cmc["position_tier"] == "rb1"
    # Projections landed where the whole simulator reads them
    assert cmc["points"]["2024"]["projected_points"] == pytest.approx(325.0)


def test_sync_is_rerunnable_unlike_csv_upload(client, stub_sources, league_id):
    client.post("/rankings/refresh")
    assert client.post(f"/league/{league_id}/player/sync").status_code == 200
    # CSV upload would 400 here; sync replaces
    response = client.post(f"/league/{league_id}/player/sync")
    assert response.status_code == 200
    assert len(response.json()["players"]["players"]) == len(ESPN_RECORDS)


def test_synced_league_reaches_draft_readiness_without_players_csv(
    client, stub_sources, league_id
):
    """Exit criteria: full player population with no players CSV; the
    remaining CSVs (historical) are later phases' targets"""
    client.post("/rankings/refresh")
    client.post(f"/league/{league_id}/player/sync")
    for url, filename in [
        (f"/league/{league_id}/historical_player", "historical_players.csv"),
        (f"/league/{league_id}/historical_draft", "historical_drafts.csv"),
    ]:
        assert upload(client, url, sample(filename)).status_code == 200
    league = client.get(f"/league/{league_id}").json()
    assert league["ready_for_draft"] is True
