# -*- coding: utf-8 -*-
"""
Seeded Monte Carlo soundness validator.

Executes the real simulation entry points (monte_carlo_draft,
scarcity_analysis) with fixed seeds and count-bounded iterations, and
asserts statistical soundness with tolerance-based checks: determinism,
exact iteration counts, no NaN/degenerate outputs, plausible value
ranges, cross-seed stability, and probability bounds. Every failure
prints WHY. Exit 0 = sound.

Run from backend/:  python scripts/validate_monte_carlo.py
No Mongo, no network — the league is synthetic and fully in-memory.
"""
import math
import os
import sys

os.environ.setdefault("DRAFT_YEAR", "2024")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import monte_carlo_draft, scarcity_analysis  # noqa: E402
from models.player import Player, Players  # noqa: E402
from models.team import League, Team  # noqa: E402

FAILURES = []


def check(name, condition, detail):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}: {detail}")
    if not condition:
        FAILURES.append(name)


def make_player(name, position, points, tier=None, nfl_team="KC"):
    return Player(
        name=name,
        position=position,
        nfl_team=nfl_team,
        tier=tier,
        points={"2024": {"projected_points": points, "actual_points": None}},
    )


def build_league():
    """Two teams, snake draft, every position stocked; RB/QB run deeper
    than the total pick count so no pool can exhaust mid-rollout."""
    players = (
        [make_player(f"RB{i}", "rb", 220.0 - 7 * i, tier=1 + i // 3) for i in range(13)]
        + [make_player(f"QB{i}", "qb", 300.0 - 11 * i, tier=1 + i // 3) for i in range(13)]
        + [make_player(f"WR{i}", "wr", 180.0 - 9 * i, tier=1 + i // 3) for i in range(8)]
        + [make_player(f"TE{i}", "te", 140.0 - 8 * i, tier=1 + i // 3) for i in range(8)]
        + [make_player(f"DST{i}", "dst", 110.0 - 6 * i) for i in range(8)]
        + [make_player(f"K{i}", "k", 120.0 - 5 * i) for i in range(8)]
    )
    return League(
        teams=[
            Team(name="Me", owner="me", draft_order=1, simulator=True),
            Team(name="Them", owner="them", draft_order=2),
        ],
        name="validator",
        round_size=6,
        current_draft_turn=0,
        copy_for_draft=False,
        players=Players(players=players),
        logistic_regression_variables={
            "x": [1, 2, 3, 4, 5, 6, 7, 8],
            "y": ["QB", "RB", "RB", "QB", "RB", "QB", "QB", "RB"],
        },
    )


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def main():
    iterations = 200

    # --- Monte Carlo draft ---------------------------------------------------
    r1 = monte_carlo_draft(build_league(), iterations=iterations, seed=101)
    r2 = monte_carlo_draft(build_league(), iterations=iterations, seed=101)
    r3 = monte_carlo_draft(build_league(), iterations=iterations, seed=202)

    check(
        "mc-determinism",
        r1.model_dump() == r2.model_dump(),
        "same seed twice must give identical results",
    )
    check(
        "mc-iteration-bound",
        r1.iterations == iterations,
        f"requested {iterations}, got {r1.iterations}",
    )

    averages = {p: getattr(r1, p) for p in ("qb", "rb", "wr", "te")}
    for position, avg in averages.items():
        check(
            f"mc-finite-{position}",
            finite(avg),
            f"{position} average is {avg!r} (NaN/inf/degenerate is unsound)",
        )
    # A full starting lineup projects a few hundred points in this pool;
    # generous bounds catch sign errors and blowups, not tuning drift
    for position in ("qb", "rb"):
        avg = averages[position]
        check(
            f"mc-range-{position}",
            100.0 < avg < 3000.0,
            f"{position} average {avg} outside plausible (100, 3000)",
        )
    # Cross-seed stability: at n=200 the position averages should agree
    # within 20% — deterministic given the two fixed seeds, so no flake
    for position in ("qb", "rb"):
        a, b = averages[position], getattr(r3, position)
        rel = abs(a - b) / max(abs(a), 1e-9)
        check(
            f"mc-stability-{position}",
            rel <= 0.20,
            f"seed 101 vs 202: {a} vs {b} (rel diff {rel:.3f} > 0.20)",
        )

    # --- Scarcity availability engine ---------------------------------------
    s1 = scarcity_analysis(build_league(), max_iterations=100, seed=7)
    s2 = scarcity_analysis(build_league(), max_iterations=100, seed=7)
    exclude = {"elapsed_seconds"}
    check(
        "scarcity-determinism",
        s1.model_dump(exclude=exclude) == s2.model_dump(exclude=exclude),
        "same seed twice must give identical reports",
    )
    check(
        "scarcity-iteration-bound",
        s1.iterations == 100,
        f"requested 100, got {s1.iterations}",
    )
    for pos in s1.positions:
        for label, prob in (
            ("prob_tier_at_next_pick", pos.prob_tier_at_next_pick),
        ):
            ok = prob is None or (finite(prob) and 0.0 <= prob <= 1.0)
            check(
                f"scarcity-prob-{pos.position}",
                ok,
                f"{pos.position} {label}={prob!r} must be in [0, 1]",
            )
        expected = pos.expected_at_next_pick
        ok = expected is None or (
            finite(expected) and 0.0 <= expected <= max(pos.remaining_now, 0) + 1e-9
        )
        check(
            f"scarcity-expected-{pos.position}",
            ok,
            f"{pos.position} expected_at_next_pick={expected!r} must be in "
            f"[0, remaining_now={pos.remaining_now}]",
        )

    print()
    if FAILURES:
        print(f"UNSOUND: {len(FAILURES)} check(s) failed: {', '.join(FAILURES)}")
        return 1
    print("SOUND: all seeded Monte Carlo checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
