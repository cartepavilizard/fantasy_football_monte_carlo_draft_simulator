# -*- coding: utf-8 -*-
"""
E1: the trade valuation model. The pure functions are unit-tested with
hand-built contexts (no Mongo); the worked examples in the spec's §7 and
Appendix A are turned into assertions here (tolerance +/- 0.1), and the
two endpoints are driven through the in-memory engine like the rest of
the cached-only read path.

The two value units are load-bearing for six later tasks, so these tests
pin the arithmetic, not just the shape.
"""
import asyncio
import datetime
from types import SimpleNamespace

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.config import DRAFT_YEAR
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueTeamInfo,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.trade_valuation import (
    ValuationContext,
    availability_curve,
    build_context,
    evaluate_trade,
    expected_points,
    player_value,
    team_ros_points,
    validate_trade,
    _rate_from,
)

SEASON = DRAFT_YEAR
LEAGUE_ID = 111

# Appendix A shared setup: w0=8, horizon 8-16 (H=9), playoff window 14-16,
# rr(WR)=7.8, rr(RB)=6.9, rr(K)=7.9, matchup tilts neutral for checkability.
RR = {"WR": 7.8, "RB": 6.9, "K": 7.9}


def make_ctx(
    specs,
    replacement,
    slot_counts=None,
    rosters=None,
    team_names=None,
    w0=8,
    w_final=16,
):
    """Hand-build a ValuationContext. specs: pid -> {position, rate, name?,
    injury_status?, bye?, team?, espn_team_id?}. A player with a bye (or an
    explicit team) gets a real NFL team and opponents for every non-bye
    horizon week; a player without one has nfl_team=None (neutral, no bye)."""
    horizon = list(range(w0, w_final + 1))
    playoff = [w for w in horizon if w in (14, 15, 16)]
    opponents = {}
    players = {}
    rates = {}
    for pid, s in specs.items():
        team = s.get("team")
        bye = s.get("bye")
        if bye is not None and team is None:
            team = f"NFL{pid}"
        if team is not None:
            for week in horizon:
                if week == bye:
                    continue
                opponents[(team, week)] = "OPP"
        players[pid] = {
            "name": s.get("name", f"P{pid}"),
            "position": s["position"],
            "nfl_team": team,
            "injury_status": s.get("injury_status"),
            "espn_team_id": s.get("espn_team_id"),
        }
        rates[pid] = s["rate"]
    team_names = team_names or {}
    league = SimpleNamespace(
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        latest_scoring_period=w0,
        final_scoring_period=17,
        lineup_slot_counts=slot_counts or {"WR": 3, "RB": 2},
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
        rosters=rosters or {},
        team_names=team_names,
        warnings=[],
    )


def approx(a, b, tol=0.1):
    return abs(a - b) <= tol


# --- availability curve (the IR-stash table) ---------------------------------


def test_availability_curve_matches_the_spec_table():
    horizon = list(range(8, 17))  # w0=8
    assert availability_curve("active", horizon)[8] == 1.0
    assert availability_curve(None, horizon)[16] == 1.0
    # questionable: only w0 is discounted
    q = availability_curve("questionable", horizon)
    assert (q[8], q[9]) == (0.75, 1.0)
    # doubtful
    d = availability_curve("doubtful", horizon)
    assert (d[8], d[9]) == (0.25, 1.0)
    # out: 0, then next-week-questionable, then full
    o = availability_curve("out", horizon)
    assert (o[8], o[9], o[10]) == (0.0, 0.75, 1.0)
    # IR: three zeroed weeks, then the return discount
    ir = availability_curve("injury_reserve", horizon)
    assert (ir[8], ir[9], ir[10], ir[11], ir[16]) == (0.0, 0.0, 0.0, 0.8, 0.8)
    # suspension shares the IR curve
    assert availability_curve("suspension", horizon) == ir


def test_availability_override_replaces_named_weeks_only():
    horizon = list(range(8, 17))
    curve = availability_curve("injury_reserve", horizon, overrides={10: 0.8})
    assert curve[10] == 0.8  # moved up
    assert curve[9] == 0.0  # untouched


# --- expected_points edge cases (bye vs missing team) ------------------------


def test_expected_points_zeroes_on_a_bye_but_not_on_missing_team():
    # known team with a bye in w0: epts(w0)=0, other weeks carry the rate
    bye_ctx = make_ctx({1: {"position": "WR", "rate": 12.0, "bye": 8}}, {"WR": 7.8})
    assert expected_points(bye_ctx, 1, 8) == 0.0
    assert approx(expected_points(bye_ctx, 1, 9), 12.0)
    # missing nfl_team: neutral multiplier, NO bye zeroing anywhere
    no_team = make_ctx({1: {"position": "WR", "rate": 12.0}}, {"WR": 7.8})
    assert approx(expected_points(no_team, 1, 8), 12.0)
    assert approx(expected_points(no_team, 1, 11), 12.0)


def test_missing_nfl_team_adds_a_warning_to_player_value():
    ctx = make_ctx({1: {"position": "WR", "rate": 12.0, "name": "No Team"}}, {"WR": 7.8})
    assert any("No Team" in w for w in player_value(ctx, 1)["warnings"])


# --- §7 / A.1: player_value and the questionable/out granularity -------------


def test_player_value_healthy_wr_from_section_7():
    # X: WR, rate 12.4, week-11 bye. Neutral (tilt-free): gross 12.4*8=99.2,
    # value 99.2 - 7.8*9 = 29.0 (§7 shows 29.9 with +0.9 tilt).
    ctx = make_ctx(
        {1: {"position": "WR", "rate": 12.4, "bye": 11, "name": "X"}}, RR
    )
    pv = player_value(ctx, 1)
    assert approx(pv["gross"], 99.2)
    assert approx(pv["value"], 29.0)
    assert approx(pv["per_week"], 3.2)
    # playoff_value from its own window: 12.4*3 - 7.8*3 = 13.8
    assert approx(pv["playoff_value"], 13.8)


def test_questionable_is_a_small_haircut_never_an_out_haircut():
    # A.1: same X tagged questionable -> gross 96.1, value 25.9; tagged out
    # -> gross 83.7, value 13.5. A Q player is never valued like an OUT one.
    q = make_ctx(
        {1: {"position": "WR", "rate": 12.4, "bye": 11, "injury_status": "questionable"}},
        RR,
    )
    o = make_ctx(
        {1: {"position": "WR", "rate": 12.4, "bye": 11, "injury_status": "out"}},
        RR,
    )
    q_value = player_value(q, 1)["value"]
    o_value = player_value(o, 1)["value"]
    assert approx(q_value, 25.9)
    assert approx(o_value, 13.5)
    assert q_value - o_value > 10  # the granularity that makes §3.2 matter


# --- §7 / A.2: IR stash value, floor, and overrides --------------------------


def _ir_ctx(injury_status="injury_reserve", overrides_ok=True):
    # Y: RB on IR, rate 14.0, week-13 bye.
    return make_ctx(
        {2: {"position": "RB", "rate": 14.0, "bye": 13, "injury_status": injury_status,
             "name": "Y"}},
        RR,
    )


def test_ir_player_floors_to_zero_but_keeps_a_playoff_component():
    ctx = _ir_ctx()
    pv = player_value(ctx, 2)
    # return weeks 11,12,14,15,16 at 0.8: gross 14*0.8*5 = 56.0; floored value 0
    assert approx(pv["gross"], 56.0)
    assert pv["value"] == 0.0
    # playoff_value is NOT zeroed by the floor: 14*0.8*3 - 6.9*3 = 12.9 > 0
    assert pv["playoff_value"] > 0
    assert approx(pv["playoff_value"], 12.9)
    # stash note names the return week and the playoff-window raw points
    assert "on IR" in pv["stash_note"]
    assert "week 11" in pv["stash_note"]


def test_availability_override_moves_value_and_never_persists():
    ctx = _ir_ctx()
    assert player_value(ctx, 2)["value"] == 0.0  # baseline floored
    # A.2: user-trusted note says Y returns week 10, not 11
    overrides = {2: {8: 0.0, 9: 0.0, 10: 0.8, 11: 0.8, 12: 0.8, 13: 0.8,
                     14: 0.8, 15: 0.8, 16: 0.8}}
    over = player_value(ctx, 2, overrides=overrides)
    # return weeks 10,11,12,14,15,16 at 0.8 (13 bye): gross 67.2, value 5.1
    assert approx(over["gross"], 67.2)
    assert approx(over["value"], 5.1)
    # the un-overridden context is untouched
    assert player_value(ctx, 2)["value"] == 0.0


# --- A.3: kicker gravity -----------------------------------------------------


def test_kicker_grades_as_a_throw_in():
    ctx = make_ctx({1: {"position": "K", "rate": 8.3}}, RR)
    pv = player_value(ctx, 1)
    assert approx(pv["value"], 3.6)  # (8.3 - 7.9) * 9
    assert pv["value"] < 5  # kickers must be throw-ins or replacement is broken


# --- A.4: 2-for-1 consolidation, fair on market, lopsided on fit -------------


def test_two_for_one_is_market_fair_but_fit_lopsided():
    specs = {
        # Team A
        1: {"position": "WR", "rate": 12.4, "espn_team_id": 1},
        2: {"position": "WR", "rate": 10.0, "espn_team_id": 1},
        3: {"position": "WR", "rate": 9.5, "espn_team_id": 1},
        4: {"position": "RB", "rate": 14.0, "espn_team_id": 1},
        5: {"position": "RB", "rate": 10.0, "espn_team_id": 1},
        6: {"position": "RB", "rate": 9.2, "espn_team_id": 1},  # bench
        # Team B
        11: {"position": "WR", "rate": 16.5, "espn_team_id": 2},
        12: {"position": "WR", "rate": 8.0, "espn_team_id": 2},
        13: {"position": "WR", "rate": 6.5, "espn_team_id": 2},
        14: {"position": "WR", "rate": 6.0, "espn_team_id": 2},  # bench
        15: {"position": "RB", "rate": 11.0, "espn_team_id": 2},
        16: {"position": "RB", "rate": 5.9, "espn_team_id": 2},
    }
    ctx = make_ctx(
        specs,
        RR,
        slot_counts={"WR": 3, "RB": 2},
        rosters={1: [1, 2, 3, 4, 5, 6], 2: [11, 12, 13, 14, 15, 16]},
        team_names={1: "Team A", 2: "Team B"},
    )
    # A sends WR-12.4 + RB-10.0; B sends WR-16.5
    result = evaluate_trade(ctx, 1, 2, [1, 5], [11])
    assert approx(result["value_sent_a"], 69.3)
    assert approx(result["value_sent_b"], 78.3)
    assert approx(result["market_gap"], -9.0)
    assert approx(result["fair_bound"], 11.7)
    assert result["verdict"] == "fair"
    # the whole point: A gains ~26.6, B breaks even
    assert approx(result["fit_delta_a"], 26.6)
    assert approx(result["fit_delta_b"], 0.0)


# --- A.5: week-1 fallback chain ----------------------------------------------


def test_rate_fallback_chain_order():
    # fallback 1: only a current-week (w0) projection
    rate, flag = _rate_from({1: 11.0}, w0=1, season_projection=None)
    assert rate == 11.0 and flag is None
    # fallback 2: no weekly number, season projection spread over 17 weeks
    rate, flag = _rate_from({}, w0=1, season_projection=153.0)
    assert rate == 9.0 and flag is None
    # fallback 3: nothing to go on
    rate, flag = _rate_from({}, w0=1, season_projection=None)
    assert rate == 0.0 and flag == "no_projection"


def test_rate_window_excludes_the_live_week():
    # §7: trailing weeks 4-7 average to 12.4; a depressed w0=8 projection
    # must NOT pull the rate (the fixtures require a strictly-before window)
    proj = {4: 11.8, 5: 13.0, 6: 12.1, 7: 12.7, 8: 3.0}
    rate, flag = _rate_from(proj, w0=8, season_projection=None)
    assert approx(rate, 12.4) and flag is None


# --- A.6: full response fixture (neutral) ------------------------------------


def test_full_trade_response_shape_and_floored_playoff_component():
    ctx = make_ctx(
        {
            101: {"position": "WR", "rate": 12.4, "bye": 11, "name": "X",
                  "espn_team_id": 3},
            202: {"position": "RB", "rate": 14.0, "bye": 13, "name": "Y",
                  "injury_status": "injury_reserve", "espn_team_id": 7},
        },
        RR,
        slot_counts={"WR": 1, "RB": 1},
        rosters={3: [101], 7: [202]},
        team_names={3: "My Team", 7: "Big Truss"},
    )
    result = evaluate_trade(ctx, 3, 7, [101], [202])
    # market: A sends 29.0, B sends 0.0 -> favors B
    assert approx(result["value_sent_a"], 29.0)
    assert result["value_sent_b"] == 0.0
    assert result["verdict"] == "favors_b"
    assert result["weeks_remaining"] == 9
    assert result["teams"]["a"]["name"] == "My Team"
    # the Y row: floored headline value, but a live playoff component
    (y,) = result["sends_b"]
    assert y["value"] == 0.0
    assert y["playoff_value"] > 0  # NOT zeroed by the headline floor
    assert y["stash_note"] and "on IR" in y["stash_note"]
    # summary quotes ROS points and per-week language from both lenses
    assert "ROS points" in result["summary"]
    assert "/week" in result["summary"]
    for key in ("fit_per_week_a", "fit_per_week_b", "fair_bound", "market_gap"):
        assert key in result


# --- validation (§6 422 cases, pure) -----------------------------------------


def test_validate_trade_flags_off_roster_and_duplicate_players():
    ctx = make_ctx(
        {1: {"position": "WR", "rate": 10.0, "espn_team_id": 1},
         2: {"position": "RB", "rate": 10.0, "espn_team_id": 2}},
        RR,
        rosters={1: [1], 2: [2]},
    )
    # player not on the claimed team
    errors = validate_trade(ctx, 1, 2, [2], [])
    assert errors and "not on team 1" in errors[0]
    # same player on both sides
    errors = validate_trade(ctx, 1, 2, [1], [1])
    assert any("both sides" in e for e in errors)
    # a clean proposal has no errors
    assert validate_trade(ctx, 1, 2, [1], [2]) == []


# --- build_context integration (the async surface) ---------------------------


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-trade")


async def _seed_context_league(engine):
    league = InSeasonLeague(
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        name="The Family League",
        team_count=2,
        latest_scoring_period=8,
        final_scoring_period=17,
        lineup_slot_counts={"QB": 1, "RB": 2, "WR": 2, "FLEX": 1, "BE": 3},
        teams=[
            LeagueTeamInfo(espn_team_id=1, name="Team A"),
            LeagueTeamInfo(espn_team_id=2, name="Team B"),
        ],
    )
    await engine.save(league)
    # player 100 (WR) trailing weeks 4-7, plus a depressed w0=8 projection
    for week, proj in [(4, 11.8), (5, 13.0), (6, 12.1), (7, 12.7), (8, 3.0)]:
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=week,
                espn_team_id=1,
                entries=[
                    RosterSlotEntry(
                        player_id=100,
                        player_name="Rate WR",
                        position="WR",
                        lineup_slot="WR",
                        projected_points=proj,
                    )
                ],
            )
        )
    # team 2 w0 roster: an RB with no free-agent baseline at its position
    await engine.save(
        TeamWeekRoster(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=8,
            espn_team_id=2,
            entries=[
                RosterSlotEntry(
                    player_id=200,
                    player_name="Lonely RB",
                    position="RB",
                    lineup_slot="RB",
                    projected_points=10.0,
                )
            ],
        )
    )
    # week-8 FA pool: three WRs so the 3rd-best is the replacement line
    await engine.save(
        FreeAgentSnapshot(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=8,
            entries=[
                FreeAgentEntry(player_id=301, player_name="FA1", position="WR",
                               projected_points=9.0),
                FreeAgentEntry(player_id=302, player_name="FA2", position="WR",
                               projected_points=8.0),
                FreeAgentEntry(player_id=303, player_name="FA3", position="WR",
                               projected_points=7.8),
            ],
        )
    )
    return league


def test_build_context_computes_rates_and_replacement():
    engine = make_engine()

    async def go():
        league = await _seed_context_league(engine)
        ctx = await build_context(engine, league)
        return ctx

    ctx = asyncio.run(go())
    # rate window is strictly before w0 -> 12.4, unmoved by the w0=8 3.0 row
    assert approx(ctx.rates[100], 12.4)
    # replacement WR = 3rd-best FA rate
    assert approx(ctx.replacement["WR"], 7.8)
    # horizon 8-16 inclusive
    assert ctx.horizon == list(range(8, 17))
    assert ctx.playoff_weeks == [14, 15, 16]
    # player_value uses both: 12.4*9 - 7.8*9 = 41.4
    pv = player_value(ctx, 100)
    assert approx(pv["value"], 41.4)
    # RB has no FA baseline -> rr absent -> inflated-values warning
    assert any("no free agents at RB" in w for w in player_value(ctx, 200)["warnings"])
    # ownership recorded so trades can be validated against w0 rosters
    assert ctx.rosters[1] == [100]
    assert ctx.rosters[2] == [200]


def test_build_context_no_free_agents_warns_and_zeroes_replacement():
    engine = make_engine()

    async def go():
        league = InSeasonLeague(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            name="No FA League",
            team_count=1,
            latest_scoring_period=8,
            final_scoring_period=17,
            lineup_slot_counts={"WR": 2},
            teams=[LeagueTeamInfo(espn_team_id=1, name="Team A")],
        )
        await engine.save(league)
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID, season=SEASON, week=8, espn_team_id=1,
                entries=[
                    RosterSlotEntry(player_id=100, player_name="WR", position="WR",
                                    lineup_slot="WR", projected_points=15.0)
                ],
            )
        )
        ctx = await build_context(engine, league)
        return player_value(ctx, 100)

    pv = asyncio.run(go())
    # no snapshot -> rr=0 -> raw points, flagged inflated
    assert any("no free agents at WR" in w for w in pv["warnings"])
    assert approx(pv["value"], pv["gross"])  # nothing subtracted


# --- endpoints ---------------------------------------------------------------


def seed_endpoint_league(app_module):
    engine = app_module.engine

    async def go():
        await engine.save(
            InSeasonLeague(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                name="The Family League",
                team_count=2,
                latest_scoring_period=8,
                final_scoring_period=17,
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
                                    lineup_slot="WR", projected_points=14.0),
                    RosterSlotEntry(player_id=2, player_name="A RB", position="RB",
                                    lineup_slot="RB", projected_points=12.0),
                ],
            )
        )
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID, season=SEASON, week=8, espn_team_id=2,
                entries=[
                    RosterSlotEntry(player_id=11, player_name="B WR", position="WR",
                                    lineup_slot="WR", projected_points=16.0),
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


def test_trade_evaluate_endpoint_grades_and_envelopes(client, app_module):
    seed_endpoint_league(app_module)
    response = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate",
        json={"team_a": 1, "team_b": 2, "sends_a": [1], "sends_b": [11]},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    data = payload["data"]
    assert data["teams"]["a"]["name"] == "Team A"
    assert data["verdict"] in ("fair", "favors_a", "favors_b")
    assert data["weeks_remaining"] == 9
    assert "summary" in data
    assert "freshness" in payload and "warnings" in payload


def test_trade_evaluate_422_off_roster_and_duplicate(client, app_module):
    seed_endpoint_league(app_module)
    # player 11 is on team 2, not team 1
    off = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate",
        json={"team_a": 1, "team_b": 2, "sends_a": [11], "sends_b": []},
    )
    assert off.status_code == 422
    assert "not on team 1" in off.json()["detail"]
    # same player both sides
    dup = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate",
        json={"team_a": 1, "team_b": 2, "sends_a": [1], "sends_b": [1]},
    )
    assert dup.status_code == 422


def test_trade_evaluate_allows_a_gift(client, app_module):
    """Empty sends on one side is a gift — graded, lopsided, not rejected."""
    seed_endpoint_league(app_module)
    response = client.post(
        f"/inseason/league/{LEAGUE_ID}/trade/evaluate",
        json={"team_a": 1, "team_b": 2, "sends_a": [1], "sends_b": []},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["value_sent_b"] == 0.0
    assert data["verdict"] == "favors_b"  # A gives everything


def test_trade_evaluate_unsynced_league_404(client, app_module):
    seed_endpoint_league(app_module)
    response = client.post(
        "/inseason/league/999/trade/evaluate",
        json={"team_a": 1, "team_b": 2, "sends_a": [1], "sends_b": [11]},
    )
    assert response.status_code == 404


def test_player_values_endpoint_for_a_team(client, app_module):
    seed_endpoint_league(app_module)
    payload = client.get(
        f"/inseason/league/{LEAGUE_ID}/player_values?espn_team_id=1"
    ).json()
    data = payload["data"]
    assert data["week"] == 8
    assert data["weeks_remaining"] == 9
    names = [v["name"] for v in data["values"]]
    assert "A WR" in names and "A RB" in names
    # sorted by value, descending
    values = [v["value"] for v in data["values"]]
    assert values == sorted(values, reverse=True)


def test_player_values_endpoint_includes_free_agents_by_position(client, app_module):
    seed_endpoint_league(app_module)
    payload = client.get(
        f"/inseason/league/{LEAGUE_ID}/player_values?position=wr"
    ).json()
    names = [v["name"] for v in payload["data"]["values"]]
    assert "FA WR1" in names  # top free agent surfaces


def test_player_values_endpoint_requires_a_selector(client, app_module):
    seed_endpoint_league(app_module)
    response = client.get(f"/inseason/league/{LEAGUE_ID}/player_values")
    assert response.status_code == 422
