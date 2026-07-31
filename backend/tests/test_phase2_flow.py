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


def _parse_synthetic_rows(rows):
    """A second push-source parser, unrelated to udk.py, for H1's registry test"""
    records = []
    problems = []
    for row in rows:
        name = row.get("Name")
        position = row.get("Pos")
        if not name or not position:
            continue
        proj = row.get("Proj")
        records.append(
            rec(
                name,
                position,
                nfl_team=row.get("Team") or None,
                projection=float(proj) if proj not in (None, "") else None,
            )
        )
    if not records:
        problems.append("no usable rows")
    return records, problems


SYNTHETIC_CSV = (
    "Name,Pos,Team,Proj\n"
    "Christian McCaffrey,RB,SF,310\n"
    "Josh Allen,QB,BUF,380\n"
).encode()


def test_generic_push_route_carries_a_registered_source_into_the_blend(
    client, five_pull_sources, monkeypatch
):
    """
    H1: PUSH_SOURCE_PARSERS is a registry, not a udk-only hardcode. A
    freshly registered source must ingest through the generic
    /rankings/push/{source} route and its values must actually reach the
    blend -- not just appear in vocabulary.
    """
    from data_sources import service

    client.post("/rankings/refresh")
    baseline_blend = client.get("/rankings/blended").json()
    baseline_cmc = next(
        r
        for r in baseline_blend["records"]
        if r["canonical_name"] == "Christian McCaffrey"
    )
    assert "synthetic" not in baseline_cmc["source_values"]

    monkeypatch.setitem(
        service.PUSH_SOURCE_PARSERS, "synthetic", _parse_synthetic_rows
    )
    upload = client.post(
        "/rankings/push/synthetic",
        files={"file": ("synthetic.csv", SYNTHETIC_CSV, "text/csv")},
    )
    assert upload.status_code == 200, upload.text
    summary = upload.json()
    assert summary["source"] == "synthetic"
    assert "synthetic" in summary["blend"]["sources_used"]

    blend = client.get("/rankings/blended").json()
    cmc = next(
        r for r in blend["records"] if r["canonical_name"] == "Christian McCaffrey"
    )
    # Not just vocabulary: the third source's projection actually moves
    # the blended number, since espn (320) + sleeper (330) alone average
    # to 325 and adding synthetic's 310 pulls that down.
    assert "synthetic" in cmc["source_values"]
    assert cmc["blended_projection"] != baseline_cmc["blended_projection"]
    assert cmc["blended_projection"] == round((320.0 + 330.0 + 310.0) / 3, 2)

    status = client.get("/rankings/status").json()
    assert status["sources"]["synthetic"]["kind"] == "push"


def test_generic_push_route_rejects_unregistered_source(client):
    response = client.post(
        "/rankings/push/not-a-real-source",
        files={"file": ("x.csv", b"Name,Pos\n", "text/csv")},
    )
    assert response.status_code == 404
    assert "not-a-real-source" in response.json()["detail"]


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
