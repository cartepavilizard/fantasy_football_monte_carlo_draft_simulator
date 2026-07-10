# -*- coding: utf-8 -*-
"""
Phase A exit criteria: a full mock draft on the shipped sample data
with tags and scarcity nudges active (A1-A6 working together).

The league is built through the real upload endpoints from the sample
CSVs; the draft is driven pick-by-pick through make_draft_pick
(use_simulator) with the stubbed draft lookup the other draft tests
use. Checkpoints assert the integrated behavior of the scarcity
engine, tag-aware suggestions, and the homer check mid-draft rather
than re-proving each unit's semantics.
"""
import asyncio

from models.config import (
    HOMER_TEAM,
    MY_GUY_TIE_FLOOR_POINTS,
    MY_GUY_TIE_PERCENT,
)
from models.suggestions import suggest_candidate
from models.team import Draft, League


class SaveOnlyEngine:
    async def save(self, obj):
        return obj


def test_full_mock_draft_with_tags_and_scarcity(app_module, client, ready_league_id):
    response = client.get(f"/league/{ready_league_id}")
    assert response.status_code == 200, response.text
    league = League(**response.json())
    assert league.ready_for_draft
    teams = len(league.teams)
    total_picks = teams * league.round_size

    # --- tag setup on the real sample pool -----------------------------------
    rbs = [p for p in league.players.rb if not p.drafted]
    avoid, runner_up, third = rbs[0], rbs[1], rbs[2]
    app_module.set_player_tag(avoid.name, "avoid", league)
    app_module.set_player_tag(third.name, "my_guy", league)

    draft = Draft(league=league)
    app_module.engine = SaveOnlyEngine()

    async def fake_get(draft_id):
        return draft

    app_module.get_a_draft_by_id = fake_get

    # --- checkpoint: scarcity nudge at pick 1 (simulator on the clock) -------
    report = app_module.scarcity_analysis(league, seconds=10, max_iterations=8)
    assert report.on_the_clock and report.iterations > 0
    assert {p.position for p in report.positions} == {"qb", "rb", "wr", "te"}
    allowed = {"reach", "wait", "toss_up", "last_chance", "exhausted", "no_tiers"}
    assert all(p.call in allowed for p in report.positions)
    assert all(p.message for p in report.positions)
    rb_report = next(p for p in report.positions if p.position == "rb")
    # The avoided RB is not an option: never in tier counts or at-risk rows
    assert avoid.name not in [p.name for p in rb_report.at_risk]
    assert rb_report.remaining_now > 0

    # --- checkpoint: tag-aware suggestion at pick 1 ---------------------------
    # Data-driven expectation from the shipped projections: the my_guy
    # wins only if it sits within the tie margin of the runner-up
    # (round 1 => no sleeper boost; the top RB is avoided)
    year = max(runner_up.points)
    margin = max(
        MY_GUY_TIE_PERCENT * runner_up.points[year].projected_points,
        MY_GUY_TIE_FLOOR_POINTS,
    )
    gap = (
        runner_up.points[year].projected_points
        - third.points[year].projected_points
    )
    expected = third.name if gap <= margin else runner_up.name
    candidate, _ = suggest_candidate(league.players.rb, 1, league.round_size)
    assert candidate.name == expected

    # --- the mock draft, paused entering the final round ----------------------
    while len(draft.league.draft_order) > teams:
        asyncio.run(app_module.make_draft_pick(draft.id, use_simulator=True))
    assert draft.league.current_draft_turn == total_picks - teams

    # --- checkpoint: tag-aware suggestions + homer check, final round ---------
    result = app_module.monte_carlo_draft(draft.league, seconds=0.5)
    assert result.iterations > 0
    assert result.suggested  # something is still suggestable in round 14
    assert "dst" in result.model_dump()  # DST/K in play after round 7
    for position, pick in result.suggested.items():
        # Wiring check: the engine's suggestion equals the tag-aware
        # candidate computed directly (final round => boost active)
        candidate, _ = suggest_candidate(
            getattr(draft.league.players, position),
            draft.league.round_size,
            draft.league.round_size,
        )
        assert pick.name == candidate.name

        # Homer checks appear exactly when the suggestion is a
        # homer-team player with at least one alternative
        player = next(
            p
            for p in getattr(draft.league.players, position)
            if p.name == pick.name
        )
        alternatives_exist = any(
            not p.drafted
            and p.nfl_team.upper() != HOMER_TEAM
            and p.tag != "avoid"
            and p.name != player.name
            for p in getattr(draft.league.players, position)
        )
        if player.nfl_team.upper() == HOMER_TEAM and alternatives_exist:
            check = result.homer_checks[position]
            assert check.suggested.name == pick.name
            assert check.alternatives and check.note
        else:
            assert position not in result.homer_checks

    # --- checkpoint: scarcity at the simulator's final pick -------------------
    final_report = app_module.scarcity_analysis(
        draft.league, seconds=10, max_iterations=5
    )
    assert final_report.final_pick
    assert final_report.your_next_pick is None
    assert all(
        p.call in {"last_chance", "exhausted", "no_tiers"}
        for p in final_report.positions
    )

    # --- finish the draft -------------------------------------------------------
    while draft.league.draft_order:
        asyncio.run(app_module.make_draft_pick(draft.id, use_simulator=True))
    assert draft.league.current_draft_turn == total_picks
    assert all(
        len(team.roster) == league.round_size for team in draft.league.teams
    )
    drafted_names = [p.name for team in draft.league.teams for p in team.roster]
    assert len(set(drafted_names)) == total_picks  # every pick a distinct player

    # A4's hard constraint held across the whole draft: the simulator
    # team auto-drafted all 14 rounds and never took the avoided player
    simulator = next(team for team in draft.league.teams if team.simulator)
    assert avoid.name not in [p.name for p in simulator.roster]
