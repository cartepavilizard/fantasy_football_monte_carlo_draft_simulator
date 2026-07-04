# -*- coding: utf-8 -*-
"""
Draft pick flow (guards L9a-L9c).

mongomock cannot resolve odmantic Reference fields, so these tests call
the route functions directly with a stubbed draft lookup instead of
going through the HTTP client.
"""
import asyncio
import csv

import pytest

from conftest import DATA_DIR
from fastapi import HTTPException
from models.player import Player, PlayerPoints, Players
from models.team import Draft, League, Team


class SaveOnlyEngine:
    async def save(self, obj):
        return obj


def sample_players():
    with open(DATA_DIR / "players.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [
        Player(
            name=row["Player"],
            position=row["Pos"],
            nfl_team=row["Team"],
            points={
                str(row["Season"]): PlayerPoints(
                    projected_points=row["Projected FFP"]
                )
            },
        )
        for row in rows
    ]


def make_draft(current_draft_turn=0, simulator=True, round_size=14):
    teams = [
        Team(name=f"T{i}", owner="O", draft_order=i, simulator=(simulator and i == 1))
        for i in (1, 2, 3)
    ]
    league = League(
        teams=teams,
        name="test",
        round_size=round_size,
        current_draft_turn=current_draft_turn,
        copy_for_draft=False,
        players=Players(players=sample_players()),
    )
    return Draft(league=league)


@pytest.fixture()
def stubbed(app_module):
    """Point the draft routes at an in-memory draft; returns a setter"""
    app_module.engine = SaveOnlyEngine()

    def use(draft):
        async def fake_get(draft_id):
            return draft

        app_module.get_a_draft_by_id = fake_get
        return draft

    return use


def test_double_pick_rejected(app_module, stubbed):
    draft = stubbed(make_draft())
    first = asyncio.run(app_module.make_draft_pick(draft.id, name="Josh Allen"))
    assert first.league.current_draft_turn == 1

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(app_module.make_draft_pick(draft.id, name="Josh Allen"))
    assert excinfo.value.status_code == 400
    assert "already drafted" in excinfo.value.detail
    assert draft.league.current_draft_turn == 1  # turn did not advance


def test_pick_after_draft_complete_rejected(app_module, stubbed):
    # 3 teams x 1 round, already at turn 3 => draft order exhausted
    draft = stubbed(make_draft(current_draft_turn=3, round_size=1))
    assert draft.league.draft_order == []

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(app_module.make_draft_pick(draft.id, name="Josh Allen"))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Draft is complete"


def test_monte_carlo_after_draft_complete_rejected(app_module, stubbed):
    draft = stubbed(make_draft(current_draft_turn=3, round_size=1))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(app_module.run_monte_carlo_simulation(draft.id))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Draft is complete"


def test_monte_carlo_without_simulator_team_rejected(app_module, stubbed):
    draft = stubbed(make_draft(simulator=False))
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(app_module.run_monte_carlo_simulation(draft.id))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "League has no simulator team"


def test_pick_name_and_simulator_flags_are_exclusive(app_module, stubbed):
    draft = stubbed(make_draft())
    for kwargs in [dict(name="X", use_simulator=True), dict()]:
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(app_module.make_draft_pick(draft.id, **kwargs))
        assert excinfo.value.status_code == 400
