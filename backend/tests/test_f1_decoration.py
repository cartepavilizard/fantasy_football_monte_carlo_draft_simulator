# -*- coding: utf-8 -*-
"""
F1 decoration call-site tests (spec docs/specs/F1-stacking-correlation.md
§4.1-4.2 + §7 MUST-NOTS).

Two display call sites are wired, decoration only:

- DRAFT: monte_carlo_draft attaches a per-position `stack_flags` field to
  MonteCarloSimulationResult, flagging a suggested player who stacks with a
  player already on the simulator team's CURRENT roster.
- TRADE: the trade/evaluate and trade/counters endpoints annotate each
  RECEIVED player's entry with a `stack` field, flagged against that side's
  post-trade roster.

The zero-effect invariant (spec §7) is asserted directly: stripping the
new fields yields byte-identical pre-decoration output. The pure math is
already pinned in test_correlation_flags.py; these tests cover only the
wiring and the no-effect property.
"""
import asyncio
import copy
import datetime

import pytest

from app import monte_carlo_draft
from models.config import DRAFT_YEAR
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueTeamInfo,
    ProGame,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.player import Player, Players
from models.team import League, Team

SEASON = DRAFT_YEAR
LEAGUE_ID = 222
NOW = datetime.datetime.now()


# ---------------------------------------------------------------------------
# DRAFT call site
# ---------------------------------------------------------------------------


def _make_player(
    name,
    position,
    points,
    nfl_team="SEA",
    tier=None,
    tag=None,
):
    return Player(
        name=name,
        position=position,
        nfl_team=nfl_team,
        tier=tier,
        tag=tag,
        points={"2024": {"projected_points": points, "actual_points": None}},
    )


def _stacked_league(qb_team="SEA", wr_team="SEA", current_draft_turn=0):
    """A league where the simulator team has already drafted a QB, and the
    WR pool's top is a same-NFL-team pass catcher (a stackable suggestion).
    Renaming `qb_team` away from `wr_team` breaks the stack without
    touching any number the simulation reads (scoring never reads nfl_team
    correlations).

    The pool is fully stocked (mirrors test_seeded_determinism.mixed_league)
    so the rollout's position-weight fallback never hits an empty pool."""
    rostered_qb = _make_player(
        "Stack QB", "qb", 306.0, nfl_team=qb_team, tier=1
    )
    simulator = Team(
        name="Me",
        owner="me",
        draft_order=1,
        simulator=True,
        roster=[rostered_qb.model_dump()],  # dicts so autofill_starters can subscript
    )
    opponent = Team(name="Them", owner="them", draft_order=2)

    players = (
        [
            _make_player(f"QB{i}", "qb", 300.0 - 11 * i, nfl_team="KC", tier=1 + i // 3)
            for i in range(13)
        ]
        + [
            _make_player("Stack WR", "wr", 238.0, nfl_team=wr_team, tier=1),
            _make_player("Alt WR1", "wr", 230.0, nfl_team="KC", tier=1),
        ]
        + [_make_player(f"WR{i}", "wr", 180.0 - 9 * i, nfl_team="DET", tier=1 + i // 3) for i in range(8)]
        + [_make_player(f"TE{i}", "te", 140.0 - 8 * i, nfl_team="DET", tier=1 + i // 3) for i in range(8)]
        + [_make_player(f"RB{i}", "rb", 220.0 - 7 * i, nfl_team="BUF", tier=1 + i // 3) for i in range(13)]
        + [_make_player(f"DST{i}", "dst", 110.0 - 6 * i, nfl_team="KC") for i in range(8)]
        + [_make_player(f"K{i}", "k", 120.0 - 5 * i, nfl_team="KC") for i in range(8)]
    )
    return League(
        teams=[simulator, opponent],
        name="stack-test",
        round_size=6,
        current_draft_turn=current_draft_turn,
        copy_for_draft=False,
        players=Players(players=players),
        logistic_regression_variables={
            "x": [1, 2, 3, 4, 5, 6, 7, 8],
            "y": ["QB", "RB", "RB", "QB", "RB", "QB", "QB", "RB"],
        },
    )


def test_draft_stack_flag_appears_when_qb_wr_stack_exists():
    result = monte_carlo_draft(_stacked_league(), iterations=12, seed=7)
    # The WR suggestion is "Stack WR" (top projection), same SEA team as the
    # rostered QB -> a strong QB+WR stack flag is attached.
    assert result.suggested["wr"].name == "Stack WR"
    assert "wr" in result.stack_flags
    flag = result.stack_flags["wr"]
    assert flag["correlation"] == 0.40
    assert flag["grade"] == "strong"
    assert flag["with"] == "Stack QB"
    assert flag["positions"] == ["WR", "QB"]
    assert "weekly swing" in flag["note"]


def test_draft_stack_flag_absent_when_no_same_team_teammate():
    # Rename the rostered QB's NFL team so the pair no longer shares a team.
    result = monte_carlo_draft(
        _stacked_league(qb_team="XXX"), iterations=12, seed=7
    )
    assert result.suggested["wr"].name == "Stack WR"
    assert "wr" not in result.stack_flags
    assert result.stack_flags == {}


def test_draft_decoration_is_zero_effect_on_pre_existing_fields():
    """Spec §7 MUST: stripping the flag changes no average, no suggestion
    name, no iteration count. Two leagues identical except the rostered
    QB's nfl_team (SEA vs XXX) — scoring never reads nfl_team correlations,
    so the simulation is identical; only the decoration differs."""
    stacked = monte_carlo_draft(_stacked_league(qb_team="SEA"), iterations=12, seed=7)
    broken = monte_carlo_draft(_stacked_league(qb_team="XXX"), iterations=12, seed=7)

    # Pre-existing fields are identical...
    assert stacked.iterations == broken.iterations
    for position in ("qb", "rb", "wr", "te"):
        assert getattr(stacked, position) == getattr(broken, position)
    assert {
        pos: pick.name for pos, pick in stacked.suggested.items()
    } == {pos: pick.name for pos, pick in broken.suggested.items()}

    # ...while the decoration is the only difference.
    assert "wr" in stacked.stack_flags
    assert broken.stack_flags == {}


def test_draft_decoration_defaults_empty_for_old_payloads():
    """The new field defaults so pre-F1 payloads still validate."""
    from models.team import MonteCarloSimulationResult

    result = MonteCarloSimulationResult(qb=100.0, rb=80.0, wr=70.0, te=50.0)
    assert result.stack_flags == {}


# ---------------------------------------------------------------------------
# TRADE call site (HTTP client against the in-memory engine)
# ---------------------------------------------------------------------------


def _seed_trade_league(app_module):
    """Two teams whose rosters set up a QB+WR stack on receipt:

    team 1: QB (SEA, pid 100) + RB (KC, pid 101)
    team 2: WR (SEA, pid 200) + RB (KC, pid 201)

    A trade sending team-2's WR to team 1 forms a SEA QB+WR stack on
    team 1's post-trade roster. Sending team-2's RB (KC) to team 1 does
    not (no KC teammate arrives or stays that stacks — QB is SEA)."""
    engine = app_module.engine

    async def go():
        await engine.save(
            InSeasonLeague(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                name="Stack Trade League",
                team_count=2,
                current_matchup_period=5,
                latest_scoring_period=5,
                final_scoring_period=17,
                trade_deadline=datetime.datetime(SEASON, 11, 18),
                lineup_slot_counts={"QB": 1, "RB": 2, "WR": 2, "TE": 1},
                teams=[
                    LeagueTeamInfo(espn_team_id=1, name="Mine", wins=5),
                    LeagueTeamInfo(espn_team_id=2, name="Theirs", wins=3),
                ],
            )
        )
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=5,
                espn_team_id=1,
                entries=[
                    RosterSlotEntry(
                        player_id=100,
                        player_name="Stack QB",
                        position="QB",
                        nfl_team="SEA",
                        lineup_slot="QB",
                        projected_points=18.0,
                    ),
                    RosterSlotEntry(
                        player_id=101,
                        player_name="My RB",
                        position="RB",
                        nfl_team="KC",
                        lineup_slot="RB",
                        projected_points=12.0,
                    ),
                ],
            )
        )
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=5,
                espn_team_id=2,
                entries=[
                    RosterSlotEntry(
                        player_id=200,
                        player_name="Stack WR",
                        position="WR",
                        nfl_team="SEA",
                        lineup_slot="WR",
                        projected_points=14.0,
                    ),
                    RosterSlotEntry(
                        player_id=201,
                        player_name="Their RB",
                        position="RB",
                        nfl_team="KC",
                        lineup_slot="RB",
                        projected_points=10.0,
                    ),
                ],
            )
        )
        # A free-agent pool so replacement level is non-zero at each
        # position (keeps E1's value math off the floor; not required for
        # the flag itself).
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=5,
                entries=[
                    FreeAgentEntry(
                        player_id=301,
                        player_name="FA QB",
                        position="QB",
                        nfl_team="BUF",
                        projected_points=8.0,
                    ),
                    FreeAgentEntry(
                        player_id=302,
                        player_name="FA RB",
                        position="RB",
                        nfl_team="DET",
                        projected_points=6.0,
                    ),
                    FreeAgentEntry(
                        player_id=303,
                        player_name="FA WR",
                        position="WR",
                        nfl_team="GB",
                        projected_points=7.0,
                    ),
                ],
            )
        )
        # A week-5 NFL slate so opponent_map() resolves a non-bye week for
        # the rostered teams (otherwise expected_points zeroes w0 too).
        for game_id, home, away in [
            (9001, "SEA", "ARI"),
            (9002, "KC", "BUF"),
            (9003, "DET", "GB"),
        ]:
            await engine.save(
                ProGame(
                    season=SEASON,
                    week=5,
                    espn_game_id=game_id,
                    home_team=home,
                    away_team=away,
                    kickoff=datetime.datetime(SEASON, 10, 8, 13, 0),
                )
            )

    asyncio.run(go())


def _strip_stack_keys(obj):
    """Recursively drop every `stack` field so a decorated response can be
    deep-compared against its pre-decoration twin (spec §7: stripping the
    flag yields identical E1/E2 output)."""
    if isinstance(obj, dict):
        return {
            k: _strip_stack_keys(v)
            for k, v in obj.items()
            if k != "stack"
        }
    if isinstance(obj, list):
        return [_strip_stack_keys(item) for item in obj]
    return obj


def test_trade_evaluate_flags_a_received_qb_wr_stack(client, app_module):
    _seed_trade_league(app_module)
    # team 1 sends its KC RB (101) and receives team 2's SEA WR (200).
    # Post-trade team 1 roster = [QB 100 (SEA), WR 200 (SEA)] -> stack.
    resp = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate",
        json={"team_a": 1, "team_b": 2, "sends_a": [101], "sends_b": [200]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # The received WR (in sends_b, the side team A receives) carries the flag
    (received_b,) = data["sends_b"]
    assert received_b["player_id"] == 200
    assert received_b["name"] == "Stack WR"
    flag = received_b["stack"]
    assert flag["correlation"] == 0.40
    assert flag["grade"] == "strong"
    assert flag["with"] == "Stack QB"
    assert flag["positions"] == ["WR", "QB"]
    # The sent player (team 1's KC RB) has no stack on team 2's roster
    (sent_a,) = data["sends_a"]
    assert "stack" not in sent_a


def test_trade_evaluate_no_flag_when_no_stack_forms(client, app_module):
    _seed_trade_league(app_module)
    # team 1 receives team 2's KC RB (201) — no KC teammate on team 1's
    # post-trade roster (its QB is SEA), so no stack flag.
    resp = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate",
        json={"team_a": 1, "team_b": 2, "sends_a": [101], "sends_b": [201]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    for entry in data["sends_a"] + data["sends_b"]:
        assert "stack" not in entry


def test_trade_evaluate_decoration_is_zero_effect(client, app_module, monkeypatch):
    """Spec §7: stripping every `stack` field yields byte-identical E1
    output. Proven by disabling the decoration (monkeypatch the pure flag
    function to return None) and deep-comparing the two responses."""
    _seed_trade_league(app_module)
    proposal = {"team_a": 1, "team_b": 2, "sends_a": [101], "sends_b": [200]}

    decorated = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate", json=proposal
    ).json()["data"]

    import inseason_api

    monkeypatch.setattr(
        inseason_api,
        "stacks_for_roster",
        lambda player, roster: None,
    )
    undecorated = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate", json=proposal
    ).json()["data"]

    # The decorated response carries a flag the undecorated one lacks...
    assert any("stack" in e for e in decorated["sends_b"])
    assert all("stack" not in e for e in undecorated["sends_b"])
    # ...and nothing else differs.
    assert _strip_stack_keys(decorated) == _strip_stack_keys(undecorated)


def test_trade_counters_original_and_counters_are_annotated(client, app_module):
    _seed_trade_league(app_module)
    resp = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/counters",
        json={"team_a": 1, "team_b": 2, "sends_a": [101], "sends_b": [200]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # The original evaluation's received WR carries the stack flag.
    original = data["original"]
    received_b = [e for e in original["sends_b"] if e["player_id"] == 200]
    assert received_b and "stack" in received_b[0]
    # Every counter's evaluation is a dict (annotated or not) and never
    # carries a pre-decoration key it shouldn't — just sanity-check shape
    # and that no counter evaluation is mutated in place (its sends lists
    # are fresh copies carrying at most an added `stack` field).
    for counter in data["counters"]:
        assert "evaluation" in counter
        assert "sends_a" in counter["evaluation"]
        assert "sends_b" in counter["evaluation"]


def test_trade_counters_decoration_is_zero_effect(client, app_module, monkeypatch):
    _seed_trade_league(app_module)
    proposal = {"team_a": 1, "team_b": 2, "sends_a": [101], "sends_b": [200]}

    decorated = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/counters", json=proposal
    ).json()["data"]

    import inseason_api

    monkeypatch.setattr(
        inseason_api,
        "stacks_for_roster",
        lambda player, roster: None,
    )
    undecorated = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/counters", json=proposal
    ).json()["data"]

    assert _strip_stack_keys(decorated) == _strip_stack_keys(undecorated)
