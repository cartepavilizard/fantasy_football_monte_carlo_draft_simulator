# -*- coding: utf-8 -*-
"""
Tag effects in the suggestion engine (A4).

Pure tests cover models/suggestions.py (avoid filter, my_guy tie-break
margin, sleeper boost curve). Integration tests use RB-only pools so
every simulated pick is forced, making outcomes exact: the simulator
team never drafts an avoid, opponents still do (the Monte Carlo market
stays undistorted), and monte_carlo_draft's suggested map reflects tags.
"""
from app import (
    draft_player,
    fit_logistic_regression_model,
    monte_carlo_draft,
    scarcity_analysis,
    simulate_pick,
)
from models.config import SLEEPER_MAX_BOOST
from models.player import Player, Players
from models.suggestions import sleeper_boost, suggest_candidate
from models.team import League, LogisticRegressionVariables, Team

import pytest


def make_player(name, position="rb", points=100.0, tag=None, tier=None, drafted=False):
    return Player(
        name=name,
        position=position,
        nfl_team="SEA",
        tag=tag,
        tier=tier,
        drafted=drafted,
        points={"2024": {"projected_points": points, "actual_points": None}},
    )


# --- avoid: filtered from all suggestions regardless of projection ----------------


def test_avoid_never_suggested_even_when_best():
    pool = [make_player("Best", points=300, tag="avoid"), make_player("Next", points=250)]
    player, _ = suggest_candidate(pool, 1, 14)
    assert player.name == "Next"


def test_all_avoided_suggests_nothing():
    pool = [make_player("A", tag="avoid"), make_player("B", tag="avoid")]
    assert suggest_candidate(pool, 1, 14) == (None, "")


def test_drafted_players_are_not_candidates():
    pool = [make_player("Gone", points=300, drafted=True), make_player("Here", points=200)]
    player, _ = suggest_candidate(pool, 1, 14)
    assert player.name == "Here"


# --- my_guy: wins ties within max(3% of best, 5 pts) ------------------------------


def test_my_guy_wins_inside_the_margin():
    # margin = max(0.03 * 300, 5) = 9; a 7-point gap is a tie
    pool = [make_player("Best", points=300), make_player("Mine", points=293, tag="my_guy")]
    player, reason = suggest_candidate(pool, 1, 14)
    assert player.name == "Mine"
    assert "my_guy tie-break" in reason


def test_my_guy_loses_outside_the_margin():
    pool = [make_player("Best", points=300), make_player("Mine", points=285, tag="my_guy")]
    player, reason = suggest_candidate(pool, 1, 14)
    assert player.name == "Best"
    assert reason == ""


def test_the_margin_floor_governs_at_low_projections():
    # margin = max(0.03 * 60 = 1.8, 5) = 5; a 4-point gap is a tie
    pool = [make_player("Best", points=60), make_player("Mine", points=56, tag="my_guy")]
    player, _ = suggest_candidate(pool, 1, 14)
    assert player.name == "Mine"


def test_best_my_guy_wins_among_multiple():
    pool = [
        make_player("Best", points=300),
        make_player("MineA", points=295, tag="my_guy"),
        make_player("MineB", points=292, tag="my_guy"),
    ]
    player, _ = suggest_candidate(pool, 1, 14)
    assert player.name == "MineA"


# --- sleeper: late-round consideration boost ---------------------------------------


def test_sleeper_boost_curve():
    assert sleeper_boost(1, 14) == 0
    assert sleeper_boost(7, 14) == 0  # exactly the ramp start (50%)
    assert sleeper_boost(11, 14) == pytest.approx(0.0857, abs=0.001)
    assert sleeper_boost(14, 14) == pytest.approx(SLEEPER_MAX_BOOST)
    assert sleeper_boost(3, 0) == 0  # degenerate round count


def test_sleeper_flips_selection_late_but_not_early():
    pool = [make_player("Best", points=120), make_player("Upside", points=110, tag="sleeper")]
    early, _ = suggest_candidate(pool, 3, 14)
    assert early.name == "Best"
    late, reason = suggest_candidate(pool, 14, 14)  # 110 * 1.15 = 126.5 > 120
    assert late.name == "Upside"
    assert "sleeper boost" in reason


def test_sleeper_boost_never_touches_projections():
    sleeper = make_player("Upside", points=110, tag="sleeper")
    suggest_candidate([make_player("Best", points=120), sleeper], 14, 14)
    assert sleeper.points["2024"].projected_points == 110


# --- integration: the simulator's behavior vs. the market -------------------------


def pick_model():
    return fit_logistic_regression_model(
        LogisticRegressionVariables(
            x=[1, 2, 3, 4, 5, 6], y=["RB", "WR", "RB", "WR", "RB", "WR"]
        )
    )


def rb_league(specs, round_size=4):
    """
    Two teams (Me = simulator, picks first; snake) over an RB-only pool,
    so simulated picks are deterministic. specs = [(points, tag, tier)]
    """
    players = [
        make_player(f"RB{i}", points=points, tag=tag, tier=tier)
        for i, (points, tag, tier) in enumerate(specs)
    ]
    return League(
        teams=[
            Team(name="Me", owner="me", draft_order=1, simulator=True),
            Team(name="Them", owner="them", draft_order=2),
        ],
        name="test",
        round_size=round_size,
        copy_for_draft=False,
        players=Players(players=players),
        logistic_regression_variables={
            "x": [1, 2, 3, 4, 5, 6],
            "y": ["RB", "WR", "RB", "WR", "RB", "WR"],
        },
    )


def test_simulator_skips_avoid_but_opponents_draft_it():
    league = rb_league(
        [(300, "avoid", 1), (290, None, 1), (280, None, 2), (270, None, 2)]
    )
    model = pick_model()
    # The simulator is on the clock: best non-avoid player
    assert simulate_pick(league, model) == "RB1"
    draft_player("RB1", league)
    # The opponent's turn: the avoid tag means nothing to the market
    assert simulate_pick(league, model) == "RB0"


def test_simulator_takes_an_avoid_over_crashing_when_nothing_else_is_left():
    league = rb_league([(300, "avoid", 1), (290, "avoid", 1)], round_size=1)
    assert simulate_pick(league, pick_model()) == "RB0"


def test_monte_carlo_suggested_map_reflects_tags():
    # margin = max(0.03 * 290, 5) = 8.7; the my_guy sits 5 back — a tie
    league = rb_league(
        [
            (300, "avoid", 1),
            (290, None, 1),
            (285, "my_guy", 1),
            (240, None, 2),
            (230, None, 2),
        ],
        round_size=2,
    )
    result = monte_carlo_draft(league, seconds=0.5)
    assert result.iterations > 0
    assert result.rb > 0
    assert result.suggested["rb"].name == "RB2"
    assert result.suggested["rb"].tag == "my_guy"
    assert "my_guy tie-break" in result.suggested["rb"].reason
    assert "qb" not in result.suggested  # no players => no suggestion
    assert result.qb == 0


def test_scarcity_excludes_avoid_from_options_but_not_from_the_market():
    league = rb_league(
        [(300, "avoid", 1), (290, None, 1), (280, None, 2), (270, None, 2), (260, None, 2)]
    )
    report = scarcity_analysis(league, seconds=5, max_iterations=10)
    rb = next(p for p in report.positions if p.position == "rb")
    # RB0 is not an option: tier 1 depth is RB1 alone
    assert rb.remaining_now == 1
    assert [p.name for p in rb.at_risk] == ["RB1"]
    # But RB0 is still in the simulated market: the two opponent picks
    # before my next turn consume RB0 and RB1, leaving tier 2 untouched.
    # (If avoid were dropped from the market, RB2 would be gone instead.)
    assert rb.prob_tier_at_next_pick == 0
    assert rb.next_tier_expected_at_next_pick == 3
