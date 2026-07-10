# -*- coding: utf-8 -*-
"""
Simulation math: distributions, tiers, and pick weighting (guards L1-L5)
"""
import csv
import random

import pytest

from app import create_historical_distributions, fit_logistic_regression_model, simulate_pick
from conftest import DATA_DIR
from fastapi import HTTPException
from models.player import Player, PlayerPoints, Players
from models.team import League, LogisticRegressionVariables, Team


def historical_rows():
    with open(DATA_DIR / "historical_players.csv", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def make_players(rows):
    return [
        Player(
            name=f"{row['Player']} ({row['Season']})",
            position=row["Pos"],
            nfl_team=row["Team"],
            points={
                str(row["Season"]): PlayerPoints(
                    projected_points=row["Projected FFP"],
                    actual_points=row.get("Actual FFP"),
                )
            },
        )
        for row in rows
    ]


def test_zero_point_season_kept_in_distributions():
    """A 0-actual-points (injury) season must contribute a -1.0 sample (L1)"""
    rows = historical_rows()
    injured = dict(rows[0])
    injured.update({"Player": "Injured", "Actual FFP": "0", "Projected FFP": "300"})
    distributions = create_historical_distributions(
        Players(players=make_players(rows + [injured])), draft_year="2024"
    )
    qb_samples = (
        distributions.qb1 + distributions.qb2 + distributions.qb3
    )
    assert -1.0 in qb_samples


def test_zero_projection_does_not_divide_by_zero():
    rows = historical_rows()
    zero_projection = dict(rows[0])
    zero_projection.update(
        {"Player": "ZeroProj", "Actual FFP": "50", "Projected FFP": "0"}
    )
    create_historical_distributions(
        Players(players=make_players(rows + [zero_projection])),
        draft_year="2024",
    )


def test_multi_season_tiers_assigned_per_season():
    """Each season's tier counts must match a single-season load (L5)"""
    from collections import Counter

    rows = historical_rows()
    single = Players(players=make_players(rows))
    baseline = Counter(p.position_tier for p in single.players)

    second_season = [dict(r, Season="2022") for r in rows]
    multi = Players(players=make_players(rows + second_season))
    for season in ("2023", "2022"):
        tiers = Counter(
            p.position_tier for p in multi.players if season in p.points
        )
        assert tiers == baseline


def draft_model():
    with open(DATA_DIR / "historical_drafts.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return fit_logistic_regression_model(
        LogisticRegressionVariables(
            x=[r["Pick"] for r in rows], y=[r["Pos"] for r in rows]
        )
    )


def test_simulate_pick_never_selects_filled_position():
    """A team with its QB slot filled must never be handed a QB (L2/L3)"""
    with open(DATA_DIR / "players.csv", encoding="utf-8-sig") as f:
        pool = make_players(list(csv.DictReader(f)))
    # Strip the season suffix make_players adds so names stay unique
    qb_roster = {
        "name": "Rostered QB",
        "position": "qb",
        "nfl_team": "X",
        "points": {"2024": {"projected_points": 300.0, "actual_points": None}},
    }
    teams = [
        Team(name="Me", owner="O", draft_order=1, roster=[qb_roster]),
        Team(name="Them", owner="O", draft_order=2),
    ]
    league = League(
        teams=teams,
        name="test",
        players=Players(players=pool),
        copy_for_draft=False,
    )
    model = draft_model()
    positions = {p.name: p.position for p in league.players.players}

    random.seed(1234)
    for _ in range(100):
        name = simulate_pick(league, model)
        assert positions[name] != "qb", "picked a QB for a team with QB filled"


def test_single_class_training_data_is_a_400():
    """Unusable regression data surfaces its cause instead of a bare 500 (L12)"""
    with pytest.raises(HTTPException) as excinfo:
        fit_logistic_regression_model(
            LogisticRegressionVariables(x=[1, 2, 3], y=["QB", "QB", "QB"])
        )
    assert excinfo.value.status_code == 400
