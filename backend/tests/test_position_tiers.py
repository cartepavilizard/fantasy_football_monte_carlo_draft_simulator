# -*- coding: utf-8 -*-
"""
Per-league position tier cutoffs.

PositionTiers cutoffs are a TEAM COUNT, not a roster length: a qb1
cutoff of QB_SIZE * team_count is the number of league-wide startable
QBs. This module pins the for_team_count classmethod, the backward-
compatible ROSTER_SIZE default, the Players validator's use of an
optional team_count, the ready_players round-trip (no re-tiering on
load), and the POST /league/{id}/player/sync endpoint threading the
league's real team count into the materialized draft board.
"""
import pytest

from conftest import upload

from data_sources.base import BaseSourceAdapter, SourceFetchError, SourceRecord

from models.config import ROSTER_SIZE, QB_SIZE, RB_SIZE, WR_SIZE, TE_SIZE, K_SIZE, DST_SIZE
from models.player import Player, PlayerPoints, Players
from models.position import PositionTiers


# --- PositionTiers.for_team_count -------------------------------------------------


def test_for_team_count_10_exact_formula_values():
    tiers = PositionTiers.for_team_count(10)
    assert tiers.qb == {"1": QB_SIZE * 10, "2": QB_SIZE * 2.5 * 10}
    assert tiers.rb == {"1": RB_SIZE * 0.5 * 10, "2": RB_SIZE * 2.5 * 10}
    assert tiers.wr == {"1": WR_SIZE * 0.5 * 10, "2": WR_SIZE * 2.5 * 10}
    assert tiers.te == {"1": TE_SIZE * 10, "2": TE_SIZE * 2 * 10}
    assert tiers.k == {"1": K_SIZE * 10, "2": K_SIZE * 2 * 10}
    assert tiers.dst == {"1": DST_SIZE * 10, "2": DST_SIZE * 2 * 10}


def test_for_team_count_12_exact_formula_values():
    tiers = PositionTiers.for_team_count(12)
    assert tiers.qb == {"1": QB_SIZE * 12, "2": QB_SIZE * 2.5 * 12}
    assert tiers.rb == {"1": RB_SIZE * 0.5 * 12, "2": RB_SIZE * 2.5 * 12}
    assert tiers.wr == {"1": WR_SIZE * 0.5 * 12, "2": WR_SIZE * 2.5 * 12}
    assert tiers.te == {"1": TE_SIZE * 12, "2": TE_SIZE * 2 * 12}
    assert tiers.k == {"1": K_SIZE * 12, "2": K_SIZE * 2 * 12}
    assert tiers.dst == {"1": DST_SIZE * 12, "2": DST_SIZE * 2 * 12}


def test_for_team_count_10_and_12_differ_for_every_position():
    t10 = PositionTiers.for_team_count(10)
    t12 = PositionTiers.for_team_count(12)
    for position in ("qb", "rb", "wr", "te", "k", "dst"):
        assert t10.model_dump()[position] != t12.model_dump()[position]


def test_default_position_tiers_matches_roster_size_backward_compat():
    """PositionTiers() with no argument keeps the ROSTER_SIZE-derived
    defaults — existing stored documents and un-migrated callers depend
    on this."""
    default = PositionTiers()
    assert default.qb == {"1": QB_SIZE * ROSTER_SIZE, "2": QB_SIZE * 2.5 * ROSTER_SIZE}
    assert default.rb == {"1": RB_SIZE * 0.5 * ROSTER_SIZE, "2": RB_SIZE * 2.5 * ROSTER_SIZE}
    assert default.wr == {"1": WR_SIZE * 0.5 * ROSTER_SIZE, "2": WR_SIZE * 2.5 * ROSTER_SIZE}
    assert default.te == {"1": TE_SIZE * ROSTER_SIZE, "2": TE_SIZE * 2 * ROSTER_SIZE}
    assert default.k == {"1": K_SIZE * ROSTER_SIZE, "2": K_SIZE * 2 * ROSTER_SIZE}
    assert default.dst == {"1": DST_SIZE * ROSTER_SIZE, "2": DST_SIZE * 2 * ROSTER_SIZE}


def test_for_team_count_zero_falls_back_to_roster_size():
    assert PositionTiers.for_team_count(0).model_dump() == PositionTiers().model_dump()


def test_for_team_count_none_falls_back_to_roster_size():
    """A None team_count must not zero the cutoffs (which would dump
    every player into tier 3); it falls back to ROSTER_SIZE."""
    assert PositionTiers.for_team_count(None).model_dump() == PositionTiers().model_dump()


def test_for_team_count_negative_falls_back_to_roster_size():
    assert PositionTiers.for_team_count(-3).model_dump() == PositionTiers().model_dump()


# --- Players validator uses team_count -------------------------------------------


def qb_pool(n=40, season="2024"):
    """n QBs with strictly descending projected points so within-position
    rank is well-defined and stable"""
    return [
        Player(
            name=f"QB {i}",
            position="qb",
            nfl_team="X",
            points={season: PlayerPoints(projected_points=float(500 - i))},
        )
        for i in range(n)
    ]


def test_players_team_count_10_yields_10_qb1s():
    players = Players(players=qb_pool(40), team_count=10)
    qb1s = [p for p in players.qb if p.position_tier == "qb1"]
    assert len(qb1s) == 10


def test_players_team_count_12_yields_12_qb1s_same_pool():
    """The SAME 40-QB pool tiers differently: 12 qb1s at 12 teams, and
    the 11th-best QB flips from qb2 (10 teams) to qb1 (12 teams)."""
    pool = qb_pool(40)
    at_10 = Players(players=pool, team_count=10)
    # Rebuild with a fresh pool because the validator mutates position_tier
    at_12 = Players(players=qb_pool(40), team_count=12)
    assert sum(1 for p in at_10.qb if p.position_tier == "qb1") == 10
    assert sum(1 for p in at_12.qb if p.position_tier == "qb1") == 12
    # The 11th-best QB (index 10, 0-based) is qb2 at 10 teams, qb1 at 12
    eleventh_10 = next(p for p in at_10.qb if p.name == "QB 10")
    eleventh_12 = next(p for p in at_12.qb if p.name == "QB 10")
    assert eleventh_10.position_tier == "qb2"
    assert eleventh_12.position_tier == "qb1"


def test_players_no_team_count_uses_roster_size_default():
    """No team_count keeps the old ROSTER_SIZE-derived cutoffs."""
    players = Players(players=qb_pool(40))
    qb1s = [p for p in players.qb if p.position_tier == "qb1"]
    assert len(qb1s) == QB_SIZE * ROSTER_SIZE


def test_players_ready_players_round_trip_not_retiered():
    """A document loaded back with ready_players=True must NOT be
    re-tiered: the stored position_tier labels survive a round trip
    even when the cutoffs that produced them no longer apply."""
    # First materialize under a 10-team cutoff so we have known labels
    built = Players(players=qb_pool(40), team_count=10)
    dumped = built.model_dump()
    # Simulate a Mongo round trip: ready_players=True short-circuits the
    # validator, so the qb1 labels (10 of them) are preserved as stored
    # even though ROSTER_SIZE would produce a different count.
    reloaded = Players(**dumped)
    qb1s = [p for p in reloaded.qb if p.position_tier == "qb1"]
    assert len(qb1s) == 10


# --- Endpoint: POST /league/{id}/player/sync threads real team count ------------


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


def _qb_records(n):
    """n QBs with strictly descending projections, plus a couple of each
    other position so the blend is non-trivial"""
    qbs = [rec(f"QB {i}", "QB", nfl_team="X", projection=float(500 - i), adp=float(i)) for i in range(n)]
    extras = [
        rec("RB 1", "RB", nfl_team="X", projection=300.0, adp=1.0),
        rec("WR 1", "WR", nfl_team="X", projection=280.0, adp=2.0),
        rec("TE 1", "TE", nfl_team="X", projection=200.0, adp=3.0),
        rec("DST 1", "DST", nfl_team="X", projection=120.0, adp=110.0),
        rec("K 1", "K", nfl_team="X", projection=140.0, adp=130.0),
    ]
    return qbs + extras


@pytest.fixture()
def stub_qb_source(monkeypatch):
    """One stub source carrying a 15-QB pool so team-count cutoffs matter"""
    from data_sources import service

    def fake_build_adapters(sources=None):
        return {"espn": StubAdapter("espn", _qb_records(15))}

    monkeypatch.setattr(service, "build_adapters", fake_build_adapters)


def _teams_csv(n):
    rows = ["Name,Owner,Simulator,Order"]
    for i in range(n):
        sim = "1" if i == 0 else "0"
        rows.append(f"Team {i + 1},Owner,{sim},{i + 1}")
    return "\n".join(rows).encode()


def test_sync_produces_tiers_matching_league_team_count(client, stub_qb_source):
    """POST /league/{id}/player/sync tiers the draft board to the league's
    real team count: a 10-team league gets exactly 10 qb1s and a 12-team
    league gets exactly 12 from the SAME blend."""
    client.post("/rankings/refresh")

    # 10-team league
    ten = upload(client, "/league", _teams_csv(10))
    assert ten.status_code == 200, ten.text
    ten_id = ten.json()["id"]
    resp = client.post(f"/league/{ten_id}/player/sync")
    assert resp.status_code == 200, resp.text
    ten_league = resp.json()
    ten_qb1s = [p for p in ten_league["players"]["qb"] if p["position_tier"] == "qb1"]
    assert len(ten_qb1s) == 10

    # 12-team league, same blend
    twelve = upload(client, "/league", _teams_csv(12))
    assert twelve.status_code == 200, twelve.text
    twelve_id = twelve.json()["id"]
    resp = client.post(f"/league/{twelve_id}/player/sync")
    assert resp.status_code == 200, resp.text
    twelve_league = resp.json()
    twelve_qb1s = [p for p in twelve_league["players"]["qb"] if p["position_tier"] == "qb1"]
    assert len(twelve_qb1s) == 12
