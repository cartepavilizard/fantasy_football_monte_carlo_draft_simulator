# -*- coding: utf-8 -*-
"""
Homer check methodology (A6).

The comparison must stay neutral: tag-blind alternative ranking (raw
projections; only avoid excluded), signed gaps where positive favors
the homer pick, and a factual note with no recommendation.
"""
from app import monte_carlo_draft
from models.homer import homer_check
from models.player import Player, Players
from models.team import League, Team


def make_player(
    name,
    nfl_team="SEA",
    position="rb",
    points=100.0,
    tag=None,
    tier=None,
    adp=None,
    consensus_rank=None,
    drafted=False,
):
    return Player(
        name=name,
        position=position,
        nfl_team=nfl_team,
        tag=tag,
        tier=tier,
        adp=adp,
        consensus_rank=consensus_rank,
        drafted=drafted,
        points={"2024": {"projected_points": points, "actual_points": None}},
    )


def check(candidate, pool, pick_number=10):
    return homer_check(candidate, pool=pool, pick_number=pick_number, year="2024")


def test_non_homer_candidate_gets_no_check():
    candidate = make_player("Someone", nfl_team="DET", points=200)
    pool = [candidate, make_player("Alt", nfl_team="KC", points=190)]
    assert check(candidate, pool) is None


def test_no_alternatives_gets_no_check():
    # Everyone else is a Seahawk, avoided, or already drafted
    candidate = make_player("Hawk", points=200)
    pool = [
        candidate,
        make_player("Hawk2", points=190),
        make_player("Avoided", nfl_team="KC", points=185, tag="avoid"),
        make_player("Gone", nfl_team="DET", points=180, drafted=True),
    ]
    assert check(candidate, pool) is None


def test_alternatives_are_top_non_homer_players_limited_to_three():
    candidate = make_player("Hawk", points=200)
    pool = [candidate, make_player("Hawk2", points=195)] + [
        make_player(f"Alt{i}", nfl_team="KC", points=190 - i) for i in range(5)
    ]
    result = check(candidate, pool)
    assert [row.name for row in result.alternatives] == ["Alt0", "Alt1", "Alt2"]
    assert all(row.nfl_team != "SEA" for row in result.alternatives)


def test_alternative_ranking_is_tag_blind_but_tags_are_displayed():
    # The my_guy projects lower, so it must NOT outrank the raw best —
    # no tie-break, no sleeper boost in the debiasing instrument
    candidate = make_player("Hawk", points=200)
    pool = [
        candidate,
        make_player("Best", nfl_team="KC", points=195),
        make_player("Mine", nfl_team="DET", points=192, tag="my_guy"),
    ]
    result = check(candidate, pool)
    assert [row.name for row in result.alternatives] == ["Best", "Mine"]
    assert result.alternatives[1].tag == "my_guy"


def test_gap_signs_and_note_when_the_alternative_is_better():
    candidate = make_player("Hawk", points=180, consensus_rank=40)
    pool = [candidate, make_player("Best", nfl_team="KC", points=192.5, consensus_rank=25)]
    result = check(candidate, pool)
    assert result.projection_gap == -12.5  # negative favors the alternative
    assert result.market_gap == -15  # market prefers the alternative too
    assert "projects 12.5 pts below" in result.note
    assert "ranks Hawk 15 spots behind Best" in result.note


def test_gap_signs_when_the_homer_pick_is_legitimately_better():
    candidate = make_player("Hawk", points=200, consensus_rank=20)
    pool = [candidate, make_player("Alt", nfl_team="KC", points=190, consensus_rank=28)]
    result = check(candidate, pool)
    assert result.projection_gap == 10
    assert result.market_gap == 8
    assert "projects 10.0 pts above" in result.note


def test_market_gap_none_when_ranks_are_missing():
    candidate = make_player("Hawk", points=200)  # no consensus rank (CSV path)
    pool = [candidate, make_player("Alt", nfl_team="KC", points=190, consensus_rank=30)]
    result = check(candidate, pool)
    assert result.market_gap is None
    assert "Market consensus" not in result.note


def test_adp_vs_pick_math_and_in_season_call_sites():
    candidate = make_player("Hawk", points=200, adp=14.5)
    pool = [candidate, make_player("Alt", nfl_team="KC", points=190, adp=8.0)]
    with_pick = check(candidate, pool, pick_number=10)
    assert with_pick.suggested.adp_vs_pick == 4.5
    assert with_pick.alternatives[0].adp_vs_pick == -2.0
    # C9 (waivers/trades) calls without a pick number
    in_season = check(candidate, pool, pick_number=None)
    assert in_season.suggested.adp_vs_pick is None
    assert in_season.pick_number is None


def test_monte_carlo_attaches_homer_checks_only_for_homer_suggestions():
    players = [
        make_player("Hawk RB", points=300, tier=1),  # SEA, will be suggested
        make_player("Alt RB", nfl_team="KC", points=290, tier=1),
        make_player("Alt RB2", nfl_team="DET", points=280, tier=2),
        make_player("QB1", nfl_team="BUF", position="qb", points=310),
        make_player("QB2", nfl_team="KC", position="qb", points=305),
    ]
    league = League(
        teams=[
            Team(name="Me", owner="me", draft_order=1, simulator=True),
            Team(name="Them", owner="them", draft_order=2),
        ],
        name="test",
        round_size=2,
        copy_for_draft=False,
        players=Players(players=players),
        logistic_regression_variables={
            "x": [1, 2, 3, 4, 5, 6],
            "y": ["RB", "QB", "RB", "QB", "RB", "QB"],
        },
    )
    result = monte_carlo_draft(league, seconds=0.5)
    assert result.suggested["rb"].name == "Hawk RB"
    assert "rb" in result.homer_checks
    rb_check = result.homer_checks["rb"]
    assert rb_check.homer_team == "SEA"
    assert rb_check.pick_number == 1
    assert [row.name for row in rb_check.alternatives] == ["Alt RB", "Alt RB2"]
    # The QB suggestion is not a Seahawk: no check attached
    assert result.suggested["qb"].name == "QB1"
    assert "qb" not in result.homer_checks
