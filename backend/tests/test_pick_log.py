# -*- coding: utf-8 -*-
"""
PickLogEntry: every pick that flows through
add_player_to_current_draft_turn_team lands in League.pick_log in
order, with the exact pick number, player, and team — the record the
draft board UI renders.
"""
from app import draft_player, monte_carlo_draft
from tests.test_seeded_determinism import mixed_league


def test_picks_are_logged_in_order_with_teams():
    league = mixed_league()
    draft_player("QB0", league)
    draft_player("RB0", league)
    draft_player("WR0", league)

    log = league.pick_log
    assert [e.pick_number for e in log] == [1, 2, 3]
    assert [e.player_name for e in log] == ["QB0", "RB0", "WR0"]
    # Two-team snake, order Me(0) then Them(1): picks 1,2 belong to
    # Me/Them; pick 3 starts round 2 back with Them
    assert [e.team_name for e in log] == ["Me", "Them", "Them"]
    assert log[0].position == "qb"


def test_simulated_drafts_do_not_pollute_the_real_log():
    league = mixed_league()
    result = monte_carlo_draft(league, iterations=8, seed=3)
    assert result.iterations == 8
    # monte_carlo_draft only ever drafts inside deep copies
    assert league.pick_log == []
