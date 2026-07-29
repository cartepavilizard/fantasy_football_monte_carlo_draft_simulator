# -*- coding: utf-8 -*-
"""
VALUE-OVER-WAIT DECISION RULE

The replacement for the four-branch full-draft Monte Carlo position
recommendation. Instead of simulating the entire remaining draft per
position and scoring the final lineup (noisy and structurally biased),
this rule asks the question that actually decides an early pick:
WHAT DOES WAITING COST ME?

For each position, compare the best player available NOW against the
best player expected to still be there at the simulator's NEXT turn.
The points difference is deterministic in projections (no scoring
noise at all); only the much-coarser "who survives" question needs the
Monte Carlo simulation, which converges far faster.
"""
import random
import time
from typing import Dict, List, Set, Tuple

from fastapi import HTTPException

from .config import DRAFT_YEAR

# Every position the caller may want to display (qb/rb/wr/te always,
# plus dst/k so the UI can show them too)
POSITIONS = ["qb", "rb", "wr", "te", "dst", "k"]

# Skill positions: the fallback when nothing else is eligible so the
# engine always has a position to recommend
SKILL_POSITIONS = ["qb", "rb", "wr", "te"]

# When the top two eligible positions' costs of waiting fall within this
# many points, the recommendation names the leader but flags the tie so
# the user knows either pick is defensible.
TIE_MARGIN_POINTS = 5.0


def simulate_picked_sequences(
    league,
    draft_pick_model,
    horizon: int,
    simulator: int,
    *,
    seconds: float,
    max_iterations: int,
    seed=None,
) -> Tuple[List[List[str]], int]:
    """
    Run `horizon` upcoming draft picks `max_iterations` times (wall-clock
    bounded by `seconds` when `seed` is None), skipping the simulator's
    own slots without drafting (the simulator's choice is the thing
    being evaluated) and drafting opponents via the logistic model.

    Returns (picked_sequences, iterations) where each picked sequence
    is the ordered list of opponent-drafted player names across the
    `horizon` slots for that rollout. Extracted verbatim from the old
    scarcity_analysis loop.

    Seed semantics: a non-None `seed` makes the run reproducible (it
    seeds the worker's module-level RNG and disables the wall-clock
    early-out, leaving max_iterations the only stop condition).
    """
    # Lazy import: app.py imports this module, so importing simulate_pick
    # / draft_player at module load would create a circular import.
    from app import simulate_pick, draft_player

    if seed is not None:
        random.seed(seed)
    picked_sequences: List[List[str]] = []
    iterations = 0
    start_time = time.time()
    while horizon > 0 and iterations < max_iterations:
        league_copy = league.model_copy(deep=True)
        picked: List[str] = []
        for _ in range(horizon):
            if league_copy.draft_order[0] == simulator:
                # Skip the simulator's slot: assignment re-validation
                # advances draft_order without removing a player
                league_copy.current_draft_turn += 1
            else:
                name = simulate_pick(league_copy, draft_pick_model)
                draft_player(name, league_copy)
                picked.append(name)
        picked_sequences.append(picked)
        iterations += 1
        if seed is None and time.time() - start_time > seconds:
            break
    return picked_sequences, iterations


def _projection(player, year) -> float:
    points = player.points.get(year)
    if points is None:
        return 0.0
    return points.projected_points


def best_available_now(pools: Dict[str, list], year) -> Dict[str, float]:
    """
    The highest projected points among each pool's undrafted players
    (0.0 for an empty pool). A player's projection is
    `player.points[year].projected_points`.
    """
    out: Dict[str, float] = {}
    for position, pool in pools.items():
        if not pool:
            out[position] = 0.0
            continue
        out[position] = max(_projection(player, year) for player in pool)
    return out


def expected_best_available(
    pools: Dict[str, list],
    picked_sequences: List[List[str]],
    year,
    cutoff: int = None,
) -> Dict[str, float]:
    """
    For EACH rollout compute the best projected points at that position
    among players not in that rollout's picked set, then average across
    rollouts. This is an average of per-rollout maxima — NOT the max of
    per-player survival-weighted averages, and not a survival-probability
    approximation.

    `cutoff` slices each rollout's ordered pick list to its first `cutoff`
    entries before building the taken set. The cost-of-waiting rule uses
    this to evaluate the board at the simulator's own upcoming pick
    (t1) rather than the t2 horizon: the players taken in the first t1
    slots of a rollout are exactly the ones gone before the simulator
    picks. When `cutoff` is None the whole picked sequence is used, which
    is the t2 behavior the rule has always had.
    """
    out: Dict[str, float] = {}
    n = len(picked_sequences)
    for position, pool in pools.items():
        if not pool or n == 0:
            out[position] = 0.0
            continue
        per_rollout_max: List[float] = []
        for picked in picked_sequences:
            taken = picked[:cutoff] if cutoff is not None else picked
            picked_set = set(taken)
            survivors = [
                _projection(player, year)
                for player in pool
                if player.name not in picked_set
            ]
            per_rollout_max.append(max(survivors) if survivors else 0.0)
        out[position] = sum(per_rollout_max) / n
    return out


def cost_of_waiting(
    value_now: Dict[str, float], value_at_next: Dict[str, float]
) -> Dict[str, float]:
    """
    The deterministic points cost of waiting one turn: now minus later,
    rounded to two decimals. Never negative in practice; do not clamp.
    """
    return {
        position: round(value_now.get(position, 0.0) - value_at_next.get(position, 0.0), 2)
        for position in value_now
    }


def eligible_positions(team, round_num: int, total_rounds: int, position_sizes) -> Set[str]:
    """
    The roster-needs guard that stops the rule chasing one position off
    a cliff. Rules:

    - capacity for a position is `position_sizes[p]`, PLUS 1 for "rb"
      and "wr" to account for the flex slot;
    - a position is eligible only while the team rosters fewer than its
      capacity (counted from the team's per-position lists — .qb, .rb,
      .wr, .te, .dst, .k);
    - "dst" and "k" are never eligible until the final 3 rounds
      (`round_num > total_rounds - 3`);
    - if those rules leave nothing eligible, return every skill position
      (qb/rb/wr/te) rather than an empty set — the engine must always be
      able to recommend something.
    """
    if hasattr(position_sizes, "model_dump"):
        sizes = position_sizes.model_dump()
    else:
        sizes = dict(position_sizes)

    eligible: Set[str] = set()
    for position in POSITIONS:
        if position in ("dst", "k") and not (round_num > total_rounds - 3):
            continue
        capacity = sizes[position] + (1 if position in ("rb", "wr") else 0)
        if len(getattr(team, position)) < capacity:
            eligible.add(position)

    if not eligible:
        eligible = set(SKILL_POSITIONS)
    return eligible


def _build_reason(cost: Dict[str, float], eligible) -> str:
    if not eligible:
        return "No eligible positions to recommend."
    ranked = sorted(eligible, key=lambda p: cost.get(p, 0.0), reverse=True)
    p1 = ranked[0]
    c1 = cost.get(p1, 0.0)
    if len(ranked) >= 2:
        p2 = ranked[1]
        c2 = cost.get(p2, 0.0)
        if c1 - c2 <= TIE_MARGIN_POINTS:
            return (
                f"{p1.upper()} {c1:.1f} and {p2.upper()} {c2:.1f} are within "
                f"{TIE_MARGIN_POINTS:.0f} pts; either is defensible"
            )
        return f"waiting costs {c1:.1f} pts at {p1.upper()} vs {c2:.1f} pts at {p2.upper()}"
    return f"waiting costs {c1:.1f} pts at {p1.upper()}"


def cost_of_waiting_report(
    league,
    draft_pick_model,
    *,
    seconds: float = 20.0,
    max_iterations: int = 150,
    seed=None,
    year: str = str(DRAFT_YEAR),
) -> dict:
    """
    Public entry point: the cost-of-waiting recommendation at the
    simulator's upcoming pick. Returns a dict with exactly these keys:
    value_now, value_at_next_pick, cost_of_waiting (each
    {position: float}), eligible_positions (a sorted list),
    recommended_position (str or None), recommended_pick (str or None),
    recommendation_reason (a short human sentence), your_next_pick (the
    overall pick number of the simulator's next turn, or None), and
    iterations (the rollout count).
    """
    simulator_team = [i for i, t in enumerate(league.teams) if t.simulator]
    if not simulator_team:
        raise HTTPException(status_code=400, detail="League has no simulator team")
    simulator = simulator_team[0]
    simulator_slots = [
        i for i, t in enumerate(league.draft_order) if t == simulator
    ]
    if not simulator_slots:
        raise HTTPException(
            status_code=400, detail="Simulator team has no remaining picks"
        )

    t1 = simulator_slots[0]
    t2 = simulator_slots[1] if len(simulator_slots) > 1 else None

    sim_team = league.teams[simulator]
    round_num = (
        league.current_draft_turn // len(league.teams) + 1 if league.teams else 1
    )
    total_rounds = league.round_size
    eligible = eligible_positions(
        sim_team, round_num, total_rounds, sim_team.position_sizes
    )

    # When there is no next pick, waiting costs nothing to compare
    # against: return zero costs and a final-pick reason.
    if t2 is None:
        zero = {position: 0.0 for position in POSITIONS}
        return {
            "value_now": dict(zero),
            "value_at_next_pick": dict(zero),
            "cost_of_waiting": dict(zero),
            "eligible_positions": sorted(eligible),
            "recommended_position": None,
            "recommended_pick": None,
            "recommendation_reason": "Final pick — waiting is not an option.",
            "your_next_pick": None,
            "iterations": 0,
        }

    horizon = t2
    pools = {
        position: [
            player
            for player in getattr(league.players, position)
            if player.drafted is False and player.tag != "avoid"
        ]
        for position in POSITIONS
    }

    picked_sequences, iterations = simulate_picked_sequences(
        league,
        draft_pick_model,
        horizon,
        simulator,
        seconds=seconds,
        max_iterations=max_iterations,
        seed=seed,
    )

    # When the simulator is on the clock (t1 == 0) the live board IS the
    # board at its upcoming pick, so value_now is the raw best available.
    # When t1 > 0 other teams pick first, so the live board overstates
    # what will actually be there: value_now is instead the expected best
    # available at the t1 checkpoint, computed from the same rollouts
    # already run for the t2 horizon by slicing each rollout's ordered
    # pick list to its first t1 entries (the players gone before the
    # simulator picks). No second simulation is run.
    if t1 == 0:
        value_now = best_available_now(pools, year)
    else:
        value_now = expected_best_available(pools, picked_sequences, year, cutoff=t1)
    value_at_next = expected_best_available(pools, picked_sequences, year)
    cost = cost_of_waiting(value_now, value_at_next)

    recommended_position = None
    if eligible:
        recommended_position = max(
            eligible, key=lambda p: cost.get(p, 0.0)
        )

    recommended_pick = None
    if recommended_position is not None:
        pool = pools.get(recommended_position, [])
        best = max(
            pool,
            key=lambda pl: _projection(pl, year),
            default=None,
        )
        if best is not None:
            recommended_pick = best.name

    reason = _build_reason(cost, eligible)
    your_next_pick = league.current_draft_turn + 1 + t2

    return {
        "value_now": value_now,
        "value_at_next_pick": value_at_next,
        "cost_of_waiting": cost,
        "eligible_positions": sorted(eligible),
        "recommended_position": recommended_position,
        "recommended_pick": recommended_pick,
        "recommendation_reason": reason,
        "your_next_pick": your_next_pick,
        "iterations": iterations,
    }
