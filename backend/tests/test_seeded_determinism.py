# -*- coding: utf-8 -*-
"""
Reproducible simulation runs: monte_carlo_draft(iterations=, seed=) and
scarcity_analysis(seed=) must be bit-for-bit deterministic given the
same seed, and the iteration bound must be exact (count-based, never
wall-clock) so seeded runs behave identically on any machine.
"""
from app import monte_carlo_draft, scarcity_analysis
from models.player import Player, Players
from models.team import League, Team


def make_player(name, position, points, tier=None, nfl_team="KC"):
    return Player(
        name=name,
        position=position,
        nfl_team=nfl_team,
        tier=tier,
        points={"2024": {"projected_points": points, "actual_points": None}},
    )


def mixed_league(current_draft_turn=0):
    """
    Two teams, snake draft, RB+QB pool with distinct projections so the
    sampled position order changes simulated outcomes — variance the
    seed must pin down.
    """
    # Every position stocked (like a real league): once a team's QB/RB
    # starters fill, the position-weight fallback samples the remaining
    # open positions, so WR/TE/DST/K pools must not be empty. RB/QB run
    # 13 deep — more than the 12 total picks — so no pool can exhaust.
    players = (
        [
            make_player(f"RB{i}", "rb", 220.0 - 7 * i, tier=1 + i // 3)
            for i in range(13)
        ]
        + [
            make_player(f"QB{i}", "qb", 300.0 - 11 * i, tier=1 + i // 3)
            for i in range(13)
        ]
        + [make_player(f"WR{i}", "wr", 180.0 - 9 * i, tier=1 + i // 3) for i in range(8)]
        + [make_player(f"TE{i}", "te", 140.0 - 8 * i, tier=1 + i // 3) for i in range(8)]
        + [make_player(f"DST{i}", "dst", 110.0 - 6 * i) for i in range(8)]
        + [make_player(f"K{i}", "k", 120.0 - 5 * i) for i in range(8)]
    )
    teams = [
        Team(name="Me", owner="me", draft_order=1, simulator=True),
        Team(name="Them", owner="them", draft_order=2),
    ]
    return League(
        teams=teams,
        name="test",
        round_size=6,
        current_draft_turn=current_draft_turn,
        copy_for_draft=False,
        players=Players(players=players),
        logistic_regression_variables={
            "x": [1, 2, 3, 4, 5, 6, 7, 8],
            "y": ["QB", "RB", "RB", "QB", "RB", "QB", "QB", "RB"],
        },
    )


def test_monte_carlo_same_seed_same_result():
    first = monte_carlo_draft(mixed_league(), iterations=24, seed=42)
    second = monte_carlo_draft(mixed_league(), iterations=24, seed=42)
    assert first.model_dump() == second.model_dump()


def test_monte_carlo_iteration_bound_is_exact():
    result = monte_carlo_draft(mixed_league(), iterations=24, seed=42)
    assert result.iterations == 24


def test_monte_carlo_different_seeds_diverge():
    a = monte_carlo_draft(mixed_league(), iterations=24, seed=1)
    b = monte_carlo_draft(mixed_league(), iterations=24, seed=2)
    averages = lambda r: (r.qb, r.rb, r.wr, r.te)  # noqa: E731
    assert averages(a) != averages(b)


def test_scarcity_same_seed_same_report():
    first = scarcity_analysis(mixed_league(), max_iterations=30, seed=7)
    second = scarcity_analysis(mixed_league(), max_iterations=30, seed=7)
    # elapsed_seconds is wall-clock bookkeeping, not simulation output
    exclude = {"elapsed_seconds"}
    assert first.model_dump(exclude=exclude) == second.model_dump(exclude=exclude)
    assert first.iterations == 30
