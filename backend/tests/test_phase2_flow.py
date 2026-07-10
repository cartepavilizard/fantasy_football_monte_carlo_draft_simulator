# -*- coding: utf-8 -*-
"""
Phase 2 exit criteria through the API: all six sources feed one blend,
sources fail independently with last-known-good fallback, the UDK file
drop joins the blend, and /rankings/status surfaces staleness.
"""
import pytest

from test_rankings_flow import (
    ESPN_RECORDS,
    FFC_RECORDS,
    SLEEPER_RECORDS,
    StubAdapter,
    rec,
)

# FantasyPros: ranks + tiers, no projections/ADP
FANTASYPROS_RECORDS = [
    rec("Patrick Mahomes", "QB", nfl_team="KC", rank=23, position_rank=2, tier=1),
    rec("Josh Allen", "QB", nfl_team="BUF", rank=18, position_rank=1, tier=1),
    rec("Christian McCaffrey", "RB", nfl_team="SF", rank=1, position_rank=1, tier=1),
    rec("Bijan Robinson", "RB", nfl_team="ATL", rank=5, position_rank=2, tier=1),
    rec("A.J. Brown", "WR", nfl_team="PHI", rank=8, position_rank=4, tier=2),
    rec("CeeDee Lamb", "WR", nfl_team="DAL", rank=2, position_rank=1, tier=1),
]

# Yahoo: ADP only, defense named by city with Yahoo-cased abbrev
YAHOO_RECORDS = [
    rec("Patrick Mahomes", "QB", nfl_team="KC", adp=24.5),
    rec("Josh Allen", "QB", nfl_team="BUF", adp=17.9),
    rec("Christian McCaffrey", "RB", nfl_team="SF", adp=1.6),
    rec("Bijan Robinson", "RB", nfl_team="ATL", adp=4.2),
    rec("Dallas", "DEF", nfl_team="Dal", adp=111.0),
]

UDK_CSV = (
    "Rank,Player,Pos,Team,Tier,Proj FFP\n"
    "1,Christian McCaffrey,RB,SF,1,322\n"
    "4,Bijan Robinson,RB,ATL,1,291\n"
    "20,Josh Allen,QB,BUF,1,392\n"
    "26,Patrick Mahomes,QB,KC,2,378\n"
    "9,AJ Brown,WR,PHI,2,258\n"
    "3,CeeDee Lamb,WR,DAL,1,283\n"
).encode()

ALL_STUBS = {
    "espn": ESPN_RECORDS,
    "sleeper": SLEEPER_RECORDS,
    "ffc": FFC_RECORDS,
    "fantasypros": FANTASYPROS_RECORDS,
    "yahoo": YAHOO_RECORDS,
}


def stub_build(records_by_source, failures=()):
    def build_adapters(sources=None):
        names = sources or list(records_by_source)
        unknown = sorted(set(names) - set(records_by_source))
        if unknown:
            raise ValueError(f"Unknown ranking sources {unknown}")
        return {
            name: (
                StubAdapter(name, error=f"{name} down")
                if name in failures
                else StubAdapter(name, records_by_source[name])
            )
            for name in names
        }

    return build_adapters


@pytest.fixture()
def five_pull_sources(monkeypatch):
    from data_sources import service

    monkeypatch.setattr(service, "build_adapters", stub_build(ALL_STUBS))


def upload_udk(client):
    return client.post(
        "/rankings/udk", files={"file": ("udk.csv", UDK_CSV, "text/csv")}
    )


def test_all_six_sources_feed_one_blend(client, five_pull_sources):
    """Phase 2 exit criterion"""
    refresh = client.post("/rankings/refresh").json()
    assert all(s["success"] for s in refresh["sources"].values())
    assert len(refresh["sources"]) == 5

    udk = upload_udk(client)
    assert udk.status_code == 200, udk.text
    summary = udk.json()
    assert summary["batch"]["anchored"] is True
    assert summary["batch"]["unresolved"] == 0  # incl. "AJ Brown" -> "A.J. Brown"
    assert set(summary["blend"]["sources_used"]) == {
        "espn",
        "sleeper",
        "ffc",
        "fantasypros",
        "yahoo",
        "udk",
    }

    blend = client.get("/rankings/blended").json()
    cmc = next(
        r for r in blend["records"] if r["canonical_name"] == "Christian McCaffrey"
    )
    assert set(cmc["source_values"]) == set(summary["blend"]["sources_used"])
    assert cmc["tier"] == 1  # tiers arrived via fantasypros/udk
    # Yahoo's city-named defense resolved through the team hint
    dal = next(r for r in blend["records"] if r["canonical_name"] == "Cowboys D/ST")
    assert "yahoo" in dal["source_values"]


def test_udk_before_any_refresh_warns_and_resolves_nothing(client):
    response = upload_udk(client)
    assert response.status_code == 200
    summary = response.json()
    assert summary["batch"]["anchored"] is False
    assert summary["batch"]["unresolved"] == summary["batch"]["records"]
    assert "refresh" in summary["warning"]


def test_udk_rejects_unusable_export(client):
    bad = b"Foo,Bar\n1,2\n"
    response = client.post(
        "/rankings/udk", files={"file": ("udk.csv", bad, "text/csv")}
    )
    assert response.status_code == 422
    assert "name" in response.json()["detail"]


def test_failed_source_falls_back_to_last_known_good(
    client, five_pull_sources, monkeypatch
):
    from data_sources import service

    client.post("/rankings/refresh")  # all five succeed and are stored

    monkeypatch.setattr(
        service, "build_adapters", stub_build(ALL_STUBS, failures={"sleeper"})
    )
    refresh = client.post("/rankings/refresh").json()
    assert refresh["sources"]["sleeper"]["success"] is False
    # The blend still includes sleeper via its previous successful batch
    assert "sleeper" in refresh["blend"]["sources_used"]


def test_status_surfaces_staleness_and_configuration(
    client, five_pull_sources, monkeypatch
):
    from data_sources import service

    client.post("/rankings/refresh")
    upload_udk(client)
    monkeypatch.setattr(
        service, "build_adapters", stub_build(ALL_STUBS, failures={"sleeper"})
    )
    client.post("/rankings/refresh")

    status = client.get("/rankings/status").json()
    sleeper = status["sources"]["sleeper"]
    assert sleeper["last_attempt"]["success"] is False
    assert "sleeper down" in sleeper["last_attempt"]["error"]
    assert sleeper["last_success"]["success"] is True
    assert sleeper["age_seconds"] >= 0
    assert status["sources"]["udk"]["kind"] == "push"
    assert status["sources"]["udk"]["last_success"]["records"] == 6
    assert status["sources"]["yahoo"]["configured"] is False  # no env creds in tests
    assert status["sources"]["fantasypros"]["access_mode"] == "page"
    assert set(status["blend"]["sources_used"]) >= {"espn", "udk"}


def test_status_before_any_activity(client):
    status = client.get("/rankings/status").json()
    assert status["blend"] is None
    assert all(s["last_attempt"] is None for s in status["sources"].values())


def test_sync_carries_udk_tiers_into_league_players(
    client, five_pull_sources, league_id
):
    client.post("/rankings/refresh")
    upload_udk(client)
    response = client.post(f"/league/{league_id}/player/sync")
    assert response.status_code == 200, response.text
    cmc = next(
        p
        for p in response.json()["players"]["players"]
        if p["name"] == "Christian McCaffrey"
    )
    assert cmc["tier"] == 1
    assert "udk" in cmc["source_values"]
