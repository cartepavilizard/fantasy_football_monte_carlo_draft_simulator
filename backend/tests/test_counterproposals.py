# -*- coding: utf-8 -*-
"""
E2: the counterproposal generator. Pure-function tests with hand-built
ValuationContexts (no Mongo), covering every §5 edge case, the four-stage
funnel's guarantees (§3), and the §6 worked example turned into assertions.
Determinism is asserted by running the search twice; the Stage-3 budget is
asserted by counting evaluate_trade calls (no full-horizon DP escapes the
funnel). One endpoint test drives the route through the in-memory engine
like the rest of the cached-only read path.

The scenarios use neutral (matchup-flat, bye-free unless a bye is named)
players so market value is exactly (rate - replacement) x H — the same
checkable arithmetic E1's own Appendix-A tests rely on.
"""
import asyncio
from types import SimpleNamespace

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.config import DRAFT_YEAR, MAX_FINALISTS
from models import counterproposals as e2
from models.counterproposals import _Search, generate_counters
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueTeamInfo,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.trade_valuation import ValuationContext, evaluate_trade

SEASON = DRAFT_YEAR
LEAGUE_ID = 111

# Replacement (zero) lines shared across the arithmetic scenarios.
RR = {"WR": 7.8, "RB": 6.9, "TE": 6.0, "K": 7.9}
H = 9  # w0=8 .. w_final=16


def make_ctx(specs, replacement, slot_counts, rosters, team_names, w0=8, w_final=16):
    """Hand-build a ValuationContext. specs: pid -> {position, rate, name?,
    espn_team_id?, byes?}. Every player is given a real NFL team with neutral
    opponents on non-bye weeks, so with strength=None the matchup multiplier
    is 1.0 and market value is exactly (rate - rr) x H (minus any bye weeks)."""
    horizon = list(range(w0, w_final + 1))
    playoff = [w for w in horizon if w in (14, 15, 16)]
    opponents, players, rates = {}, {}, {}
    for pid, s in specs.items():
        byes = s.get("byes") or []
        team = f"NFL{pid}"
        for week in horizon:
            if week not in byes:
                opponents[(team, week)] = "OPP"
        players[pid] = {
            "name": s.get("name", f"P{pid}"),
            "position": s["position"],
            "nfl_team": team,
            "injury_status": s.get("injury_status"),
            "espn_team_id": s.get("espn_team_id"),
        }
        rates[pid] = s["rate"]
    league = SimpleNamespace(
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        latest_scoring_period=w0,
        final_scoring_period=17,
        lineup_slot_counts=slot_counts,
        teams=[
            SimpleNamespace(espn_team_id=tid, name=name)
            for tid, name in team_names.items()
        ],
    )
    return ValuationContext(
        league=league,
        season=SEASON,
        w0=w0,
        horizon=horizon,
        playoff_weeks=playoff,
        opponents=opponents,
        strength=None,
        rates=rates,
        players=players,
        replacement=replacement,
        rosters=rosters,
        team_names=team_names,
        warnings=[],
    )


def approx(a, b, tol=0.1):
    return abs(a - b) <= tol


def rate_for(value, rr, h=H):
    """Rate that yields a target market value for a bye-free neutral player."""
    return rr + value / h


# ---------------------------------------------------------------------------
# §6 worked example (normative). A sends WR X (29.9) + WR Z (8.1) = 38.0; B
# sends RB Q (18.3). Gap +19.7, bound 10 -> favors_b, A disadvantaged, anchor
# RB Q. ADD candidates from B: TE R (12.4) -> new gap 7.3; WR S (22.0) -> new
# gap -2.3. Both are finalists; adding WR S craters B's fit below the floor,
# so the single returned counter is +TE R.
# ---------------------------------------------------------------------------


def _worked_example_ctx():
    specs = {
        # Team A (disadvantaged): WR surplus, RB hole. Sends X + Z, gets Q.
        1: {"position": "WR", "rate": rate_for(29.9, 7.8), "name": "X", "espn_team_id": 1},
        2: {"position": "WR", "rate": rate_for(8.1, 7.8), "name": "Z", "espn_team_id": 1},
        3: {"position": "WR", "rate": 11.0, "name": "A-WR3", "espn_team_id": 1},
        7: {"position": "WR", "rate": 10.7, "name": "A-WR4", "espn_team_id": 1},
        4: {"position": "RB", "rate": 12.0, "name": "A-RB1", "espn_team_id": 1},
        5: {"position": "RB", "rate": 7.0, "name": "A-RB2", "espn_team_id": 1},
        6: {"position": "TE", "rate": 9.0, "name": "A-TE", "espn_team_id": 1},
        # Team B (advantaged): S is B's clear #2 WR (kept in +R, lost in +S),
        # B-WR2/B-WR3 sit just inside the untouchable margin so S stays a legal
        # ADD candidate; RB depth is thin so both counters lose Q's RB value.
        11: {"position": "RB", "rate": rate_for(18.3, 6.9), "name": "Q", "espn_team_id": 2},
        12: {"position": "TE", "rate": rate_for(12.4, 6.0), "name": "R", "espn_team_id": 2},
        13: {"position": "WR", "rate": rate_for(22.0, 7.8), "name": "S", "espn_team_id": 2},
        18: {"position": "WR", "rate": 8.75, "name": "B-WR2", "espn_team_id": 2},
        19: {"position": "WR", "rate": 8.75, "name": "B-WR3", "espn_team_id": 2},
        15: {"position": "RB", "rate": 10.0, "name": "B-RB1", "espn_team_id": 2},
        16: {"position": "RB", "rate": 6.9, "name": "B-RB2", "espn_team_id": 2},
        17: {"position": "TE", "rate": 9.0, "name": "B-TE1", "espn_team_id": 2},
    }
    return make_ctx(
        specs,
        RR,
        {"WR": 2, "RB": 2, "TE": 1},
        {1: [1, 2, 3, 7, 4, 5, 6], 2: [11, 12, 13, 18, 19, 15, 16, 17]},
        {1: "Team A", 2: "Team B"},
    )


def test_worked_example_setup_and_anchor():
    ctx = _worked_example_ctx()
    search = _Search(ctx, 1, 2, [1, 2], [11], None)
    # A gives 38.0, B gives 18.3 -> gap +19.7, A disadvantaged.
    assert approx(search.value_a, 38.0)
    assert approx(search.value_b, 18.3)
    assert approx(search.gap, 19.7)
    assert search.dis_side == "a"
    # Anchor: the only player A receives is RB Q (pid 11).
    assert search.anchor == 11
    original = evaluate_trade(ctx, 1, 2, [1, 2], [11], None)
    assert original["verdict"] == "favors_b"
    assert approx(original["market_gap"], 19.7)


def test_worked_example_both_add_finalists_present():
    ctx = _worked_example_ctx()
    search = _Search(ctx, 1, 2, [1, 2], [11], None)
    by_name = {
        f["move"].get("player_name"): f for f in search.finalists()
    }
    # TE R and WR S both survive Stage 1/2 as ADD finalists...
    assert "R" in by_name and by_name["R"]["move"]["type"] == "add"
    assert "S" in by_name and by_name["S"]["move"]["type"] == "add"
    assert approx(by_name["R"]["new_gap"], 7.3)   # 19.7 - 12.4
    assert approx(by_name["S"]["new_gap"], -2.3)  # 19.7 - 22.0


def test_worked_example_returns_only_the_te_add_counter():
    ctx = _worked_example_ctx()
    result = generate_counters(ctx, 1, 2, [1, 2], [11])
    # WR S fails FIT_FLOOR for B (Stage 3) -> exactly one counter, +TE R.
    assert len(result["counters"]) == 1
    counter = result["counters"][0]
    assert counter["move"] == {
        "type": "add",
        "team": "b",
        "player_id": 12,
        "player_name": "R",
    }
    assert counter["sends_a"] == [1, 2]
    assert counter["sends_b"] == [11, 12]
    assert approx(counter["evaluation"]["market_gap"], 7.3)
    # Rationale quotes the 19.7 -> 7.3 gap close in ROS/per-week framing.
    assert "19.7" in counter["rationale"]
    assert "7.3" in counter["rationale"]
    assert "ROS pts" in counter["rationale"] and "pts/week" in counter["rationale"]
    # 2-for-2 after the counter -> no roster-size imbalance.
    assert counter["evaluation"]["roster_size_note"] is None
    assert result["note"] is None


def test_search_is_deterministic_run_twice():
    ctx = _worked_example_ctx()
    first = generate_counters(ctx, 1, 2, [1, 2], [11])
    second = generate_counters(ctx, 1, 2, [1, 2], [11])
    assert first == second  # no randomness; a pure function of the inputs


# ---------------------------------------------------------------------------
# Anchor selection (§5 anchor ambiguity): higher value wins, ties break to
# the lower player_id — deterministically.
# ---------------------------------------------------------------------------


def test_anchor_ties_break_to_lower_player_id():
    # A sends one 40-value WR; B sends two equal 12-value players. A gives
    # more -> A disadvantaged; A receives B's two equal players, so the anchor
    # is the tie broken to the lower id (21).
    specs = {
        1: {"position": "WR", "rate": rate_for(40.0, 7.8), "espn_team_id": 1},
        21: {"position": "RB", "rate": rate_for(12.0, 6.9), "espn_team_id": 2},
        22: {"position": "TE", "rate": rate_for(12.0, 6.0), "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs, RR, {"WR": 1, "RB": 1, "TE": 1},
        {1: [1], 2: [21, 22]}, {1: "A", 2: "B"},
    )
    search = _Search(ctx, 1, 2, [1], [21, 22], None)
    assert search.dis_side == "a"
    assert search.anchor == 21  # equal value -> lower id


# ---------------------------------------------------------------------------
# §5: 1-for-1 lopsided — REMOVE is unavailable (would empty a side); an ADD
# turns it into a 2-for-1 and the roster-size note fires.
# ---------------------------------------------------------------------------


def test_one_for_one_has_no_remove_and_add_yields_roster_size_note():
    # A sends a rich RB (value ~28); B sends a modest RB (value ~10). Gap ~18.
    # B (advantaged) sweetens with a surplus bench RB — B keeps a startable RB
    # after sending two, so the counter doesn't wreck B's fit.
    specs = {
        1: {"position": "RB", "rate": rate_for(28.0, 6.9), "name": "A-RB", "espn_team_id": 1},
        2: {"position": "WR", "rate": 12.0, "name": "A-WR", "espn_team_id": 1},
        3: {"position": "RB", "rate": 9.9, "name": "A-RB2", "espn_team_id": 1},  # near-equal backup
        11: {"position": "RB", "rate": rate_for(10.0, 6.9), "name": "B-RB", "espn_team_id": 2},
        12: {"position": "RB", "rate": rate_for(11.0, 6.9), "name": "B-sweet", "espn_team_id": 2},
        13: {"position": "RB", "rate": 12.0, "name": "B-RB3", "espn_team_id": 2},
        14: {"position": "WR", "rate": 13.0, "name": "B-WR", "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs, RR, {"WR": 1, "RB": 1},
        {1: [1, 2, 3], 2: [11, 12, 13, 14]}, {1: "Team A", 2: "Team B"},
    )
    result = generate_counters(ctx, 1, 2, [1], [11])
    # never a REMOVE move (each side sends exactly one)
    assert all(c["move"]["type"] != "remove" for c in result["counters"])
    add_counters = [c for c in result["counters"] if c["move"]["type"] == "add"]
    assert add_counters, "an ADD should turn the 1-for-1 into a 2-for-1"
    for counter in add_counters:
        # B ends up sending two, A one -> A ends the trade a player over.
        assert counter["evaluation"]["roster_size_note"] is not None
        assert "over" in counter["evaluation"]["roster_size_note"]


# ---------------------------------------------------------------------------
# §5: unequal player counts annotate the evaluate dict (E1 doesn't; E2 does).
# ---------------------------------------------------------------------------


def test_roster_size_note_direction_and_original_annotation():
    # 2-for-1: A sends two, B sends one. A's roster shrinks by one on
    # execution -> A ends "under", an add is required.
    note = e2._roster_size_note(
        make_ctx({}, RR, {"WR": 1}, {}, {1: "My Team", 2: "Them"}),
        1, 2, [101, 102], [201],
    )
    assert note is not None
    assert "My Team" in note and "under" in note and "add" in note
    # equal counts -> no note
    assert e2._roster_size_note(
        make_ctx({}, RR, {"WR": 1}, {}, {}), 1, 2, [1], [2]
    ) is None


def test_original_evaluation_is_annotated_with_roster_size_note():
    specs = {
        1: {"position": "WR", "rate": rate_for(25.0, 7.8), "espn_team_id": 1},
        2: {"position": "RB", "rate": rate_for(9.0, 6.9), "espn_team_id": 1},
        11: {"position": "WR", "rate": rate_for(14.0, 7.8), "espn_team_id": 2},
        12: {"position": "RB", "rate": 8.0, "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs, RR, {"WR": 1, "RB": 1},
        {1: [1, 2], 2: [11, 12]}, {1: "Team A", 2: "Team B"},
    )
    # A sends 2, B sends 1 -> the original dict carries the note.
    result = generate_counters(ctx, 1, 2, [1, 2], [11])
    assert "roster_size_note" in result["original"]
    assert result["original"]["roster_size_note"] is not None
    assert "under" in result["original"]["roster_size_note"]


# ---------------------------------------------------------------------------
# §5: gap enormous (superstar for a kicker) — no single move closes it, so an
# honest empty answer with the no-fair-counter note.
# ---------------------------------------------------------------------------


def test_enormous_gap_returns_empty_with_note():
    specs = {
        1: {"position": "WR", "rate": rate_for(100.0, 7.8), "name": "Superstar", "espn_team_id": 1},
        2: {"position": "WR", "rate": 9.0, "espn_team_id": 1},
        3: {"position": "RB", "rate": 9.0, "espn_team_id": 1},
        11: {"position": "K", "rate": rate_for(3.0, 7.9), "name": "Kicker", "espn_team_id": 2},
        12: {"position": "WR", "rate": 9.0, "espn_team_id": 2},
        13: {"position": "RB", "rate": 9.0, "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs, RR, {"WR": 1, "RB": 1, "K": 1},
        {1: [1, 2, 3], 2: [11, 12, 13]}, {1: "Team A", 2: "Team B"},
    )
    result = generate_counters(ctx, 1, 2, [1], [11])
    assert result["counters"] == []
    assert result["note"] == "no fair counter exists within one move of this proposal"


# ---------------------------------------------------------------------------
# §4: an already-fair original still runs the search but says so in note.
# ---------------------------------------------------------------------------


def test_already_fair_original_gets_strict_upgrade_note():
    specs = {
        1: {"position": "WR", "rate": rate_for(20.0, 7.8), "espn_team_id": 1},
        2: {"position": "RB", "rate": 9.0, "espn_team_id": 1},
        11: {"position": "WR", "rate": rate_for(19.0, 7.8), "espn_team_id": 2},
        12: {"position": "RB", "rate": 9.0, "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs, RR, {"WR": 1, "RB": 1},
        {1: [1, 2], 2: [11, 12]}, {1: "Team A", 2: "Team B"},
    )
    result = generate_counters(ctx, 1, 2, [1], [11])
    assert evaluate_trade(ctx, 1, 2, [1], [11], None)["verdict"] == "fair"
    assert result["note"] == (
        "original trade is already fair — counters below are strict upgrades"
    )


# ---------------------------------------------------------------------------
# §3: a SWAP closes the gap when the disadvantaged side swaps an overpriced
# outgoing player for a cheaper same-roster one. The anchor is never touched.
# ---------------------------------------------------------------------------


def test_swap_counter_replaces_overpriced_outgoing_player():
    # A (disadvantaged) sends an anchor-getter plus an overpriced WR; swapping
    # the overpriced WR for a cheaper A bench WR closes the gap.
    specs = {
        # Team A sends RB (10) + WR (30); receives nothing tradable back but Q.
        1: {"position": "RB", "rate": rate_for(10.0, 6.9), "name": "A-RB", "espn_team_id": 1},
        2: {"position": "WR", "rate": rate_for(30.0, 7.8), "name": "A-WR-rich", "espn_team_id": 1},
        3: {"position": "WR", "rate": rate_for(14.0, 7.8), "name": "A-WR-cheap", "espn_team_id": 1},
        4: {"position": "WR", "rate": 12.0, "name": "A-WR3", "espn_team_id": 1},
        5: {"position": "RB", "rate": 12.0, "name": "A-RB2", "espn_team_id": 1},
        # Team B sends a big WR the disadvantaged side anchors on.
        11: {"position": "WR", "rate": rate_for(24.0, 7.8), "name": "B-anchor", "espn_team_id": 2},
        12: {"position": "WR", "rate": 11.0, "name": "B-WR2", "espn_team_id": 2},
        13: {"position": "RB", "rate": 11.0, "name": "B-RB", "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs, RR, {"WR": 2, "RB": 1},
        {1: [1, 2, 3, 4, 5], 2: [11, 12, 13]}, {1: "Team A", 2: "Team B"},
    )
    # A sends RB(10) + rich WR(30) = 40 for B's anchor WR(24). Gap +16.
    search = _Search(ctx, 1, 2, [1, 2], [11], None)
    assert search.dis_side == "a" and search.anchor == 11
    swaps = [f for f in search.finalists() if f["move"]["type"] == "swap"]
    assert swaps, "a gap-closing swap should be a finalist"
    move = swaps[0]["move"]
    assert move["team"] == "a"
    assert move["player_out_id"] == 2  # the overpriced WR leaves
    assert move["player_id"] == 3      # the cheaper WR comes in
    assert 11 not in (move["player_id"], move["player_out_id"])  # anchor untouched


# ---------------------------------------------------------------------------
# §3/§7: the Stage-3 full-evaluation budget is capped at MAX_FINALISTS — the
# funnel never runs a full-horizon evaluation outside those <= 12 calls.
# ---------------------------------------------------------------------------


def test_stage3_evaluation_budget_is_capped(monkeypatch):
    # A wide roster with many addable pieces -> a big raw candidate pool.
    specs = {1: {"position": "WR", "rate": rate_for(40.0, 7.8), "espn_team_id": 1},
             2: {"position": "RB", "rate": 12.0, "espn_team_id": 1},
             3: {"position": "TE", "rate": 9.0, "espn_team_id": 1}}
    # B (advantaged) gets 20 addable bench pieces inside the gap band.
    for pid in range(100, 120):
        specs[pid] = {"position": "WR", "rate": rate_for(6.0 + (pid - 100) * 0.5, 7.8),
                      "espn_team_id": 2}
    specs[11] = {"position": "WR", "rate": rate_for(15.0, 7.8), "espn_team_id": 2}
    ctx = make_ctx(
        specs, RR, {"WR": 2, "RB": 1, "TE": 1},
        {1: [1, 2, 3], 2: [11] + list(range(100, 120))}, {1: "A", 2: "B"},
    )

    calls = {"n": 0}
    real_eval = e2.evaluate_trade

    def counting_eval(*args, **kwargs):
        calls["n"] += 1
        return real_eval(*args, **kwargs)

    monkeypatch.setattr(e2, "evaluate_trade", counting_eval)
    generate_counters(ctx, 1, 2, [1], [11])
    # one for the original + at most MAX_FINALISTS for the finalists
    assert calls["n"] <= MAX_FINALISTS + 1


# ---------------------------------------------------------------------------
# §5: advantaged side has no addable surplus (every bench piece is below the
# gap band) and the disadvantaged side sends a single player (no REMOVE, no
# in-band SWAP) -> honest empty answer.
# ---------------------------------------------------------------------------


def test_no_addable_surplus_returns_empty_with_note():
    specs = {
        1: {"position": "WR", "rate": rate_for(60.0, 7.8), "name": "A-star", "espn_team_id": 1},
        2: {"position": "RB", "rate": 9.0, "espn_team_id": 1},
        11: {"position": "WR", "rate": rate_for(20.0, 7.8), "name": "B-WR", "espn_team_id": 2},
        # B's only spare pieces are replacement-level scrubs (value ~0),
        # far below the gap band -> nothing to add or swap in.
        12: {"position": "RB", "rate": 6.9, "espn_team_id": 2},
        13: {"position": "WR", "rate": 7.8, "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs, RR, {"WR": 1, "RB": 1},
        {1: [1, 2], 2: [11, 12, 13]}, {1: "Team A", 2: "Team B"},
    )
    result = generate_counters(ctx, 1, 2, [1], [11])
    assert result["counters"] == []
    assert result["note"] == "no fair counter exists within one move of this proposal"


# ---------------------------------------------------------------------------
# Endpoint: POST /trade/counters, standard envelope, through the in-memory
# engine — and a 422 for an off-roster player (validation reused from E1).
# ---------------------------------------------------------------------------


def _seed_endpoint_league(app_module):
    engine = app_module.engine

    async def go():
        await engine.save(
            InSeasonLeague(
                espn_league_id=LEAGUE_ID, season=SEASON, name="The Family League",
                team_count=2, latest_scoring_period=8, final_scoring_period=17,
                lineup_slot_counts={"QB": 1, "RB": 2, "WR": 2, "FLEX": 1, "BE": 3},
                teams=[
                    LeagueTeamInfo(espn_team_id=1, name="Team A"),
                    LeagueTeamInfo(espn_team_id=2, name="Team B"),
                ],
            )
        )
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID, season=SEASON, week=8, espn_team_id=1,
                entries=[
                    RosterSlotEntry(player_id=1, player_name="A WR", position="WR",
                                    lineup_slot="WR", projected_points=20.0),
                    RosterSlotEntry(player_id=2, player_name="A WR2", position="WR",
                                    lineup_slot="WR", projected_points=12.0),
                    RosterSlotEntry(player_id=3, player_name="A RB", position="RB",
                                    lineup_slot="RB", projected_points=11.0),
                ],
            )
        )
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID, season=SEASON, week=8, espn_team_id=2,
                entries=[
                    RosterSlotEntry(player_id=11, player_name="B WR", position="WR",
                                    lineup_slot="WR", projected_points=13.0),
                    RosterSlotEntry(player_id=12, player_name="B WR2", position="WR",
                                    lineup_slot="WR", projected_points=11.0),
                    RosterSlotEntry(player_id=13, player_name="B RB", position="RB",
                                    lineup_slot="RB", projected_points=10.0),
                ],
            )
        )
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID, season=SEASON, week=8,
                entries=[
                    FreeAgentEntry(player_id=301, player_name="FA WR1",
                                   position="WR", projected_points=9.0),
                    FreeAgentEntry(player_id=302, player_name="FA WR2",
                                   position="WR", projected_points=8.0),
                    FreeAgentEntry(player_id=303, player_name="FA WR3",
                                   position="WR", projected_points=7.5),
                ],
            )
        )

    asyncio.run(go())


def test_counters_endpoint_returns_original_counters_and_note(client, app_module):
    _seed_endpoint_league(app_module)
    response = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/counters",
        json={"team_a": 1, "team_b": 2, "sends_a": [1], "sends_b": [11]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert "original" in data and "counters" in data and "note" in data
    assert isinstance(data["counters"], list)
    assert data["original"]["teams"]["a"]["name"] == "Team A"
    assert "freshness" in payload and "warnings" in payload


def test_counters_endpoint_422_off_roster(client, app_module):
    _seed_endpoint_league(app_module)
    response = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/counters",
        json={"team_a": 1, "team_b": 2, "sends_a": [11], "sends_b": []},
    )
    assert response.status_code == 422
    assert "not on team 1" in response.json()["detail"]
