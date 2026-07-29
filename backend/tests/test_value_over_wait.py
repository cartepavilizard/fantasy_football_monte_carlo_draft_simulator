# -*- coding: utf-8 -*-
"""
Value-over-wait decision rule: pure-function tests for the cost-of-waiting
math (best_available_now, expected_best_available, cost_of_waiting,
eligible_positions) plus one integration test that drives
cost_of_waiting_report through a small synthetic league so the survival
contrast is hand-checkable. No database.
"""
from types import SimpleNamespace

import pytest

from models.player import Player, Players
from models.team import League, Team
from models.value_over_wait import (
    best_available_now,
    cost_of_waiting,
    cost_of_waiting_report,
    eligible_positions,
    expected_best_available,
)


YEAR = "2024"


def make_player(name, position, points, tier=None, nfl_team="KC"):
    return Player(
        name=name,
        position=position,
        nfl_team=nfl_team,
        tier=tier,
        points={YEAR: {"projected_points": points, "actual_points": None}},
    )


def pool(position, *players):
    return {position: list(players)}


# --- best_available_now ----------------------------------------------------------


def test_best_available_now_takes_the_max_projection_per_pool():
    pools = {
        "qb": [make_player("A", "qb", 300.0), make_player("B", "qb", 250.0)],
        "rb": [make_player("C", "rb", 220.0)],
        "wr": [],  # empty pool -> 0.0
    }
    assert best_available_now(pools, YEAR) == {"qb": 300.0, "rb": 220.0, "wr": 0.0}


# --- expected_best_available -----------------------------------------------------


def test_expected_best_available_averages_per_rollout_maxima():
    # Rollout 1 takes A -> survivors {B} -> max 50
    # Rollout 2 takes B -> survivors {A} -> max 100
    # Average of per-rollout maxima = (50 + 100) / 2 = 75
    pools = pool("rb", make_player("A", "rb", 100.0), make_player("B", "rb", 50.0))
    picked_sequences = [["A"], ["B"]]
    assert expected_best_available(pools, picked_sequences, YEAR) == {"rb": 75.0}


def test_expected_best_available_is_not_the_max_of_averages():
    # Same setup as above. The WRONG max-of-averages answer is 50 (each
    # player survives half the time: max(100*0.5, 50*0.5) = 50), which is
    # not what the rule computes.
    pools = pool("rb", make_player("A", "rb", 100.0), make_player("B", "rb", 50.0))
    picked_sequences = [["A"], ["B"]]
    result = expected_best_available(pools, picked_sequences, YEAR)
    assert result["rb"] == 75.0
    assert result["rb"] != 50.0


def test_expected_best_available_empty_rollouts_is_zero():
    pools = pool("rb", make_player("A", "rb", 100.0))
    assert expected_best_available(pools, [], YEAR) == {"rb": 0.0}


def test_expected_best_available_treats_empty_pool_as_zero():
    pools = {"rb": [], "wr": [make_player("A", "wr", 100.0)]}
    picked_sequences = [["A"]]
    result = expected_best_available(pools, picked_sequences, YEAR)
    assert result == {"rb": 0.0, "wr": 0.0}


# --- cost_of_waiting -------------------------------------------------------------


def test_cost_of_waiting_is_now_minus_later_rounded_to_two():
    value_now = {"qb": 300.0, "rb": 220.5}
    value_at_next = {"qb": 100.0, "rb": 220.5}
    assert cost_of_waiting(value_now, value_at_next) == {"qb": 200.0, "rb": 0.0}


def test_cost_of_waiting_does_not_clamp():
    # Never negative in practice, but the check verifies the arithmetic
    # rather than clamping, so a synthetic inversion passes through.
    value_now = {"qb": 50.0}
    value_at_next = {"qb": 100.0}
    assert cost_of_waiting(value_now, value_at_next) == {"qb": -50.0}


# --- eligible_positions ----------------------------------------------------------


def _team_with_roster(position_players):
    """Build a Team whose roster populates the per-position starter lists"""
    roster = []
    for position, players in position_players.items():
        for i, pts in enumerate(players):
            roster.append(make_player(f"{position.upper()}{i}", position, pts).model_dump())
    return Team(name="T", owner="O", draft_order=1, roster=roster)


def test_eligible_positions_excludes_a_full_qb_roster():
    team = _team_with_roster({"qb": [300.0]})  # qb_size=1 -> full
    eligible = eligible_positions(team, 1, 14, team.position_sizes)
    assert "qb" not in eligible
    # rb/wr/te are still open (empty starters)
    assert "rb" in eligible and "wr" in eligible and "te" in eligible


def test_eligible_positions_flex_allowance_keeps_rb_eligible_when_full():
    # rb_size=2, two RBs drafted: without the flex allowance rb would be
    # full (2 == 2); the +1 flex slot keeps it eligible (2 < 3).
    team = _team_with_roster({"rb": [220.0, 210.0]})
    eligible = eligible_positions(team, 1, 14, team.position_sizes)
    assert "rb" in eligible
    # Contrast: a full TE (te_size=1, no flex) is NOT eligible
    team_te = _team_with_roster({"te": [140.0]})
    assert "te" not in eligible_positions(team_te, 1, 14, team_te.position_sizes)


def test_eligible_positions_gates_dst_and_k_until_the_final_three_rounds():
    team = _team_with_roster({})  # empty dst/k starters
    # Round 1 of 14 -> dst/k not eligible yet
    early = eligible_positions(team, 1, 14, team.position_sizes)
    assert "dst" not in early and "k" not in early
    # Round 12 of 14 (12 > 14 - 3 = 11) -> dst/k become eligible
    late = eligible_positions(team, 12, 14, team.position_sizes)
    assert "dst" in late and "k" in late


def test_eligible_positions_falls_back_to_skill_positions_when_nothing_open():
    # A stub team with every position at capacity (including the +1 flex
    # for rb/wr) and dst/k gated out by an early round. The real Team
    # model caps per-position starter lists at their size, so rb/wr can
    # never reach the +1 flex capacity; this stub exercises the guard.
    full = SimpleNamespace(
        qb=["q"],
        rb=["r", "r", "r"],
        wr=["w", "w", "w"],
        te=["t"],
        dst=["d"],
        k=["k"],
        position_sizes={"qb": 1, "rb": 2, "wr": 2, "te": 1, "flex": 1, "dst": 1, "k": 1},
    )
    eligible = eligible_positions(full, 1, 14, full.position_sizes)
    assert eligible == {"qb", "rb", "wr", "te"}


# --- cost_of_waiting_report integration ------------------------------------------


class RbStubModel:
    """Always predicts RB: opponents deterministically take the top RBs"""

    classes_ = ["RB", "WR"]

    def predict_proba(self, x):
        return [[1.0, 0.0]]


def report_league():
    """
    Two teams, snake draft, simulator on the clock at pick 1. The
    simulator's next turn is pick 4 (slots Me, Them, Them, Me), so two
    opponent picks happen before the next turn. With the RB-only stub
    model, both opponents take the top two RBs, so the elite RBs do NOT
    survive but the top WR (never touched) does.
    """
    players = (
        [make_player(f"RB{i}", "rb", 300.0 - i * 5, tier=1) for i in range(4)]
        + [make_player(f"WR{i}", "wr", 250.0 - i * 5, tier=1) for i in range(3)]
        + [make_player(f"QB{i}", "qb", 200.0 - i, tier=1) for i in range(2)]
        + [make_player(f"TE{i}", "te", 120.0 - i, tier=1) for i in range(2)]
    )
    teams = [
        Team(name="Me", owner="me", draft_order=1, simulator=True),
        Team(name="Them", owner="them", draft_order=2),
    ]
    return League(
        teams=teams,
        name="test",
        round_size=4,
        current_draft_turn=0,
        copy_for_draft=False,
        players=Players(players=players),
        logistic_regression_variables={
            "x": [1, 2, 3, 4],
            "y": ["RB", "WR", "RB", "WR"],
        },
    )


def test_cost_of_waiting_report_survival_scores_lower_cost():
    report = cost_of_waiting_report(
        report_league(),
        RbStubModel(),
        seconds=5.0,
        max_iterations=10,
        seed=7,
        year=YEAR,
    )
    cost = report["cost_of_waiting"]
    # RB's elite (RB0=300, RB1=295) both get taken by the two opponent
    # picks; the best survivor is RB2=290. cost = 300 - 290 = 10.
    # WR's elite (WR0=250) is never touched; cost = 250 - 250 = 0.
    assert cost["rb"] > cost["wr"]
    # The position whose best player survives (WR) scores LOWER cost.
    assert cost["wr"] == 0.0
    assert cost["rb"] == 10.0
    # Highest-cost eligible position is recommended
    assert report["recommended_position"] == "rb"
    assert report["recommended_pick"] == "RB0"
    # next pick is the simulator's slot t2=3 -> overall pick 1 + 3 = 4
    assert report["your_next_pick"] == 4
    assert report["iterations"] == 10


def test_cost_of_waiting_report_final_pick_returns_zero_costs():
    # round_size=1 -> simulator has a single (final) pick, no next turn
    players = [make_player("RB0", "rb", 300.0)]
    teams = [
        Team(name="Me", owner="me", draft_order=1, simulator=True),
        Team(name="Them", owner="them", draft_order=2),
    ]
    league = League(
        teams=teams,
        name="test",
        round_size=1,
        current_draft_turn=0,
        copy_for_draft=False,
        players=Players(players=players),
        logistic_regression_variables={"x": [1, 2], "y": ["RB", "WR"]},
    )
    report = cost_of_waiting_report(
        league, RbStubModel(), seconds=1.0, max_iterations=5, seed=1, year=YEAR
    )
    assert report["cost_of_waiting"] == {
        p: 0.0 for p in ["qb", "rb", "wr", "te", "dst", "k"]
    }
    assert report["recommended_position"] is None
    assert report["recommended_pick"] is None
    assert report["your_next_pick"] is None
    assert "final pick" in report["recommendation_reason"].lower()
    assert report["iterations"] == 0
