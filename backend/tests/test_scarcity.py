# -*- coding: utf-8 -*-
"""
Tier-depletion scarcity engine (A1).

Unit tests cover the pure tier/decision logic in models/scarcity.py;
the integration tests drive scarcity_analysis through leagues built so
opponents' simulated picks are forced, making the Monte Carlo
availability outcome deterministic without seeding.
"""
import asyncio

import pytest

from app import scarcity_analysis
from fastapi import HTTPException
from models.player import Player, Players
from models.scarcity import (
    REACH_PROBABILITY,
    WAIT_PROBABILITY,
    effective_tier,
    scarcity_call,
    tier_breakdown,
)
from models.team import Draft, League, Team


def make_player(name, position, tier=None, position_tier=None, points=100.0):
    data = {
        "name": name,
        "position": position,
        "nfl_team": "SEA",
        "tier": tier,
        "points": {"2024": {"projected_points": points, "actual_points": None}},
    }
    if position_tier is not None:
        data["position_tier"] = position_tier
    return Player(**data)


# --- pure tier logic --------------------------------------------------------------


def test_effective_tier_consensus_wins_over_position_tier():
    player = make_player("A", "rb", tier=4, position_tier="rb1")
    assert effective_tier(player) == 4


def test_effective_tier_falls_back_to_position_tier_digit():
    assert effective_tier(make_player("A", "rb", position_tier="rb2")) == 2
    assert effective_tier(make_player("A", "dst", position_tier="dst")) is None


def test_tier_breakdown_skips_empty_tier_numbers():
    available = [
        make_player("A", "te", tier=3),
        make_player("B", "te", tier=3),
        make_player("C", "te", tier=7),
        make_player("D", "te"),  # untiered: excluded from tier math
    ]
    tier, tier_players, next_tier, next_players = tier_breakdown(available)
    assert tier == 3
    assert {p.name for p in tier_players} == {"A", "B"}
    assert next_tier == 7
    assert [p.name for p in next_players] == ["C"]


def test_tier_breakdown_with_no_tier_data():
    assert tier_breakdown([make_player("A", "dst", position_tier="dst")]) == (
        None,
        [],
        None,
        [],
    )


# --- the directional call ---------------------------------------------------------


def call_for(prob, remaining=3, final_pick=False, tier=2, next_tier=3):
    return scarcity_call(
        position="rb",
        tier=tier,
        remaining_now=remaining,
        expected_at_next_pick=prob * remaining,
        prob_tier_at_next_pick=prob,
        next_tier=next_tier,
        next_tier_remaining_now=6,
        final_pick=final_pick,
    )


def test_call_thresholds():
    assert call_for(REACH_PROBABILITY - 0.01)[0] == "reach"
    assert call_for(REACH_PROBABILITY)[0] == "toss_up"
    assert call_for(WAIT_PROBABILITY - 0.01)[0] == "toss_up"
    assert call_for(WAIT_PROBABILITY)[0] == "wait"


def test_reach_message_names_the_last_player_case_and_next_tier_depth():
    call, message = call_for(0.2, remaining=1)
    assert call == "reach"
    assert "last player in RB tier 2" in message
    assert "Tier 3 has 6 options" in message


def test_final_pick_and_edge_calls_win_over_probabilities():
    assert call_for(0.9, final_pick=True)[0] == "last_chance"
    assert call_for(0.9, remaining=0)[0] == "exhausted"
    assert call_for(0.9, tier=None, next_tier=None)[0] == "no_tiers"


# --- the Monte Carlo availability engine ------------------------------------------


def forced_rb_league(rb_tiers, simulator_order=1, round_size=4, current_draft_turn=0):
    """
    Two teams, snake draft. The player pool is RB-only, so every
    simulated opponent pick is forced to be the best remaining RB and
    survival outcomes are exact. rb_tiers is the consensus tier per
    player, best projection first.
    """
    players = [
        make_player(f"RB{i}", "rb", tier=tier, points=200.0 - i)
        for i, tier in enumerate(rb_tiers)
    ]
    teams = [
        Team(name="Me", owner="me", draft_order=1, simulator=simulator_order == 1),
        Team(name="Them", owner="them", draft_order=2, simulator=simulator_order == 2),
    ]
    return League(
        teams=teams,
        name="test",
        round_size=round_size,
        current_draft_turn=current_draft_turn,
        copy_for_draft=False,
        players=Players(players=players),
        # Two classes keep the logistic model trainable; WR weights are
        # discarded at pick time because the pool has no WRs
        logistic_regression_variables={
            "x": [1, 2, 3, 4, 5, 6],
            "y": ["RB", "WR", "RB", "WR", "RB", "WR"],
        },
    )


def rb_report(league, **kwargs):
    report = scarcity_analysis(league, seconds=5, max_iterations=20, **kwargs)
    return report, next(p for p in report.positions if p.position == "rb")


def test_reach_when_opponents_will_exhaust_the_active_tier():
    # Snake order Me, Them, Them, Me: two opponent picks before my next
    # turn eat both tier-1 RBs => the tier never survives to pick 4
    league = forced_rb_league([1, 1, 2, 2, 2, 2, 2, 2])
    report, rb = rb_report(league)
    assert report.on_the_clock and not report.final_pick
    assert (report.your_pick, report.your_next_pick) == (1, 4)
    assert report.iterations > 0
    assert rb.tier == 1 and rb.remaining_now == 2
    assert rb.expected_at_pick == 2  # both still here at my upcoming pick
    assert rb.prob_tier_at_next_pick == 0
    assert rb.call == "reach"
    assert rb.next_tier == 2 and rb.next_tier_remaining_now == 6
    # Per-player odds: the two forced opponent picks never survive
    at_risk = {p.name: p.survival_at_next_pick for p in rb.at_risk}
    assert at_risk == {"RB0": 0, "RB1": 0}


def test_wait_when_the_active_tier_runs_deep():
    # Two opponent picks against ten tier-1 RBs => eight always survive
    league = forced_rb_league([1] * 10 + [2] * 4)
    _, rb = rb_report(league)
    assert rb.call == "wait"
    assert rb.prob_tier_at_next_pick == 1
    assert rb.expected_at_next_pick == 8


def test_availability_at_upcoming_pick_when_not_on_the_clock():
    # Them picks first (turn 1 already passed to Me? no: Me drafts 2nd)
    league = forced_rb_league([1, 1, 1, 2, 2, 2, 2, 2], simulator_order=2)
    report, rb = rb_report(league)
    assert not report.on_the_clock
    assert (report.your_pick, report.your_next_pick) == (2, 3)
    # One forced opponent pick before my turn takes the best tier-1 RB
    assert rb.remaining_now == 3
    assert rb.expected_at_pick == 2
    at_risk = {p.name: p.survival_at_pick for p in rb.at_risk}
    assert at_risk["RB0"] == 0 and at_risk["RB1"] == 1


def test_final_pick_reports_last_chance_without_simulating():
    league = forced_rb_league([1, 1, 2, 2], round_size=1)
    report, rb = rb_report(league)
    assert report.final_pick and report.your_next_pick is None
    assert report.iterations == 0  # on the clock at the final pick
    assert rb.call == "last_chance"
    assert rb.prob_tier_at_pick == 1  # deterministically still here


def test_no_remaining_picks_is_a_400():
    league = forced_rb_league([1, 1, 2, 2], round_size=1, current_draft_turn=1)
    with pytest.raises(HTTPException) as excinfo:
        scarcity_analysis(league)
    assert excinfo.value.status_code == 400
    assert "no remaining picks" in excinfo.value.detail


def test_positions_without_players_are_exhausted():
    report = scarcity_analysis(
        forced_rb_league([1, 1, 2, 2]), seconds=5, max_iterations=5
    )
    calls = {p.position: p.call for p in report.positions}
    assert set(calls) == {"qb", "rb", "wr", "te"}
    assert calls["qb"] == calls["wr"] == calls["te"] == "exhausted"


# --- the endpoint (route function called directly; see conftest note) -------------


def test_scarcity_endpoint_rejects_completed_draft(app_module):
    league = forced_rb_league([1, 1], round_size=1, current_draft_turn=2)
    draft = Draft(league=league)

    async def fake_get(draft_id):
        return draft

    app_module.get_a_draft_by_id = fake_get
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(app_module.get_draft_scarcity(draft.id))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "Draft is complete"
