# -*- coding: utf-8 -*-
"""
E4: the proactive trade opportunity scanner. Covers every edge case
enumerated in docs/specs/E4-opportunity-scanner.md §6 — the five AND-ed
trigger conditions (each failing alone), the 'questionable never pushes'
rule, the per-league-week push budget, first-pass silent seeding, dedupe
across re-scans, the doubtful-only soft row, the my-team-missing
degradation, and the on-demand report's purity (call twice, no
notifications, unchanged state). Mongo-backed via mongomock +
odmantic AIOEngine, mirroring test_trade_valuation.py / test_notifications.py.
"""
import asyncio
import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

import opportunity_api
from models.config import DRAFT_YEAR
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InSeasonLeague,
    InjuryDesignation,
    LeagueTeamInfo,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.notifications import Notification
from models.opportunity_scanner import (
    InjuryScanState,
    TRADE_SCAN_ENABLED,
    run_opportunity_scan,
    scan_league,
    trade_opportunity_report,
)

SEASON = DRAFT_YEAR
LEAGUE_ID = 111
MY_TEAM = 3
W0 = 9

LINEUP = {
    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1,
    "BE": 6, "IR": 1,
}


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-e4")


# --- roster fixtures ---------------------------------------------------------
# (pid, name, pos, rate, status, slot). nfl_team is left None for every
# rostered player so epts = rate * availability (no bye, neutral matchup),
# keeping the arithmetic exactly checkable — same trick as test_trade_valuation.

MY_ROSTER = [
    (300, "My QB", "QB", 14.0, None, "QB"),
    (301, "My RB1", "RB", 12.0, None, "RB"),
    (302, "My RB2", "RB", 11.0, None, "RB"),
    (303, "Surplus RB", "RB", 8.0, None, "BE"),     # spare (bench) + attractive (value 16)
    (304, "My WR1", "WR", 12.0, None, "WR"),
    (305, "My WR2", "WR", 11.0, None, "WR"),        # strong WR2 keeps 303 off FLEX
    (306, "FLEX WR", "WR", 9.0, None, "FLEX"),      # holds FLEX -> 303 is true bench
    (316, "My TE", "TE", 8.0, None, "TE"),
    (307, "My K", "K", 8.0, None, "K"),
    (308, "My DST", "DST", 8.0, None, "DST"),
    (309, "Bench RB", "RB", 3.0, None, "BE"),
]

RIVAL_ROSTER = [
    (700, "Rival QB", "QB", 14.0, None, "QB"),
    (701, "J. Starter", "RB", 14.1, "out", "RB"),   # the injured starter
    (702, "Handcuff", "RB", 6.2, None, "RB"),
    (703, "RB3", "RB", 4.9, None, "BE"),
    (704, "Bench WR", "WR", 10.0, None, "BE"),
    (705, "WR1", "WR", 12.0, None, "WR"),
    (715, "WR2", "WR", 11.0, None, "WR"),
    (716, "FLEX WR", "WR", 10.5, None, "FLEX"),
    (706, "Rival TE", "TE", 8.0, None, "TE"),
    (707, "Rival K", "K", 8.0, None, "K"),
    (708, "Rival DST", "DST", 8.0, None, "DST"),
]

# FA pool: 3 RBs (rr=6.0) and 3 WRs (rr=7.0); H=8 weeks (w0=9 .. 16).
FA_ENTRIES = [
    (501, "FA RB1", "RB", 8.0),
    (502, "FA RB2", "RB", 7.0),
    (503, "FA RB3", "RB", 6.0),
    (511, "FA WR1", "WR", 9.0),
    (512, "FA WR2", "WR", 8.0),
    (513, "FA WR3", "WR", 7.0),
]


def _seed_team_week(engine, team_id, players, week, season=SEASON):
    entries = [
        RosterSlotEntry(
            player_id=pid,
            player_name=name,
            position=pos,
            nfl_team=None,
            lineup_slot=slot,
            injury_status=status if week == W0 else None,
            projected_points=rate,
        )
        for (pid, name, pos, rate, status, slot) in players
    ]
    return TeamWeekRoster(
        espn_league_id=LEAGUE_ID, season=season, week=week,
        espn_team_id=team_id, entries=entries,
    )


def seed_league(
    engine,
    my_roster=MY_ROSTER,
    rivals=None,
    fa_entries=FA_ENTRIES,
    my_team=MY_TEAM,
    season=SEASON,
    league_id=LEAGUE_ID,
    w0=W0,
):
    """Seed a league with trailing-week (5-8) + current (w0) rosters and a
    w0 free-agent snapshot. `rivals` is a list of (team_id, name, roster)."""

    async def go():
        teams_rivals = rivals if rivals is not None else [(7, "Big Truss", RIVAL_ROSTER)]
        teams = [LeagueTeamInfo(espn_team_id=my_team, name="My Team")]
        for tid, tname, _ in teams_rivals:
            teams.append(LeagueTeamInfo(espn_team_id=tid, name=tname))
        await engine.save(
            InSeasonLeague(
                espn_league_id=league_id,
                season=season,
                name="Test League",
                team_count=len(teams),
                latest_scoring_period=w0,
                final_scoring_period=17,
                lineup_slot_counts=LINEUP,
                teams=teams,
            )
        )
        for week in range(w0 - 4, w0 + 1):  # 5..9
            await engine.save(_seed_team_week(engine, my_team, my_roster, week))
            for tid, _, roster in teams_rivals:
                await engine.save(_seed_team_week(engine, tid, roster, week))
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=league_id, season=season, week=w0,
                entries=[
                    FreeAgentEntry(
                        player_id=pid, player_name=name, position=pos,
                        projected_points=rate,
                    )
                    for (pid, name, pos, rate) in fa_entries
                ],
            )
        )

    asyncio.run(go())


async def set_player_status(engine, team_id, player_id, status, season=SEASON):
    """Replace a team's w0 roster so one player's injury_status flips —
    the scanner sees the new effective status on the next pass."""
    rosters = await engine.find(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == LEAGUE_ID)
        & (TeamWeekRoster.season == season)
        & (TeamWeekRoster.week == W0)
        & (TeamWeekRoster.espn_team_id == team_id),
    )
    for roster in rosters:
        for entry in roster.entries:
            if entry.player_id == player_id:
                entry.injury_status = status
        await engine.save(roster)


def seed_prior_state(engine, statuses, season=SEASON):
    """Manually seed InjuryScanState so a status change is 'new' on the
    next scan (avoids the two-scan bootstrap dance for trigger tests)."""

    async def go():
        for pid, status in statuses.items():
            await engine.save(
                InjuryScanState(
                    espn_league_id=LEAGUE_ID, season=season,
                    player_id=pid, status=status,
                    scanned_at=datetime.datetime.now(),
                )
            )

    asyncio.run(go())


def all_state(engine):
    async def go():
        return await engine.find(InjuryScanState)
    return asyncio.run(go())


def all_notifications(engine):
    async def go():
        return await engine.find(Notification)
    return asyncio.run(go())


def windows(report):
    return [o for o in report["opportunities"] if o["severity"] == "window"]


def watches(report):
    return [o for o in report["opportunities"] if o["severity"] == "watch"]


def opp_for(report, player_id):
    for o in report["opportunities"]:
        if o["injured"]["player_id"] == player_id:
            return o
    return None


# --- first-pass silent seeding (spec §4, §6) --------------------------------


def test_first_scan_seeds_state_silently_with_no_triggers(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)  # 701 already 'out' on day one

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    # everything is "new" on day one — that's bootstrap, not news
    assert report["opportunities"] == []
    assert all_notifications(engine) == []
    state = {s.player_id: s.status for s in all_state(engine)}
    assert state[701] == "out"               # seeded from current status
    assert state[300] is None                # active players seeded too


# --- the full hard trigger (all five conditions, spec §7 worked example) -----


def test_hard_trigger_creates_window_notification(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)
    # 701 was active at the last scan; now out -> a NEW real injury event
    seed_prior_state(engine, {pid: None for (pid, *_ ) in MY_ROSTER + RIVAL_ROSTER})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    wins = windows(report)
    assert len(wins) == 1
    win = wins[0]
    assert win["rival_team_id"] == 7
    assert win["rival_team_name"] == "Big Truss"
    assert win["injured"] == {
        "player_id": 701, "name": "J. Starter", "position": "RB",
        "status": "out", "rate": 14.1,
    }
    assert round(win["rival_gap_per_week"], 1) == 7.9  # 14.1 - 6.2
    assert win["my_surplus"][0]["player_id"] == 303
    assert win["probe"] is not None
    assert win["probe"]["fit_delta_a"] > 0

    notes = all_notifications(engine)
    assert len(notes) == 1
    n = notes[0]
    assert n.kind == "trade_window"
    assert n.dedupe_key == f"tradewin:{LEAGUE_ID}:{SEASON}:7:701"
    assert "Big Truss" in n.title and "J. Starter" in n.title and "out" in n.title
    assert n.week == W0
    assert "Surplus RB" in n.body


# --- condition 1: questionable NEVER triggers (spec §2.1, §8) ----------------


def test_questionable_status_never_triggers_or_pushes(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)
    asyncio.run(set_player_status(engine, 7, 701, "questionable"))
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + RIVAL_ROSTER})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    assert opp_for(report, 701) is None
    assert all_notifications(engine) == []


# --- condition 2: a backup injury is not a trade window ---------------------


def test_backup_injury_below_starter_floor_does_not_trigger(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    # 703 is a bench RB, rate 4.9 < STARTER_RATE_FLOOR, not started. Make
    # 701 healthy so only the backup injury (703) is in play.
    rival = [(7, "Big Truss", [
        (p if p[0] == 703 else (p if p[0] != 701 else (701, "J. Starter", "RB", 14.1, None, "RB")))
        for p in RIVAL_ROSTER
    ])]
    rival = [(7, "Big Truss", [
        (703, "RB3", "RB", 4.9, "out", "BE") if p[0] == 703
        else (701, "J. Starter", "RB", 14.1, None, "RB") if p[0] == 701
        else p
        for p in RIVAL_ROSTER
    ])]
    seed_league(engine, rivals=rival)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + rival[0][2]})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    assert opp_for(report, 703) is None
    assert all_notifications(engine) == []


# --- condition 3: rival has the handcuff, no window -------------------------


def test_rival_with_same_caliber_backup_has_no_window(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    # 702 rate 13.0 -> gap 14.1 - 13.0 = 1.1 < RIVAL_GAP_POINTS
    rival = [(7, "Big Truss", [
        (p if p[0] != 702 else (702, "Handcuff", "RB", 13.0, None, "RB"))
        for p in RIVAL_ROSTER
    ])]
    seed_league(engine, rivals=rival)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + rival[0][2]})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    assert opp_for(report, 701) is None
    assert all_notifications(engine) == []


# --- condition 4: spare bench fodder is not an offer ------------------------


def test_no_surplus_above_offer_floor_degrades_to_watch(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    # 303 rate 3.0 -> value 0 (< SURPLUS_VALUE_FLOOR); no surplus RB qualifies
    my = [(p if p[0] != 303 else (303, "Surplus RB", "RB", 3.0, None, "BE"))
          for p in MY_ROSTER]
    seed_league(engine, my_roster=my)
    seed_prior_state(engine, {pid: None for (pid, *_) in my + RIVAL_ROSTER})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    opp = opp_for(report, 701)
    assert opp is not None
    assert opp["severity"] == "watch"
    assert opp["my_surplus"] == []
    assert "offer floor" in opp["note"]
    assert all_notifications(engine) == []


# --- condition 5: probe not fit-positive -> watch ---------------------------


def test_probe_not_fit_positive_degrades_to_watch(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    # Rival carries NO bench: every rostered player starts, so removing any
    # one of them costs >= their full rate (> SURPLUS_COST_CEILING). Nothing
    # is movable -> no probe can be formed -> condition 5 fails.
    rival = [(7, "Big Truss", [
        (700, "Rival QB", "QB", 14.0, None, "QB"),
        (701, "J. Starter", "RB", 14.1, "out", "RB"),
        (702, "Handcuff", "RB", 6.2, None, "RB"),
        (703, "RB3", "RB", 4.9, None, "BE"),
        (705, "WR1", "WR", 12.0, None, "WR"),
        (715, "WR2", "WR", 11.0, None, "WR"),
        (706, "Rival TE", "TE", 8.0, None, "TE"),
        (707, "Rival K", "K", 8.0, None, "K"),
        (708, "Rival DST", "DST", 8.0, None, "DST"),
    ])]
    seed_league(engine, rivals=rival)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + rival[0][2]})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    opp = opp_for(report, 701)
    assert opp is not None
    assert opp["severity"] == "watch"
    assert opp["probe"] is None
    assert "probe" in opp["note"]
    assert all_notifications(engine) == []


# --- doubtful passes all five but is a soft row, never pushes (spec §3) ------


def test_doubtful_passing_all_five_is_watch_only(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    rival = [(7, "Big Truss", [
        (p if p[0] != 701 else (701, "J. Starter", "RB", 14.1, "doubtful", "RB"))
        for p in RIVAL_ROSTER
    ])]
    seed_league(engine, rivals=rival)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + rival[0][2]})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    opp = opp_for(report, 701)
    assert opp is not None
    assert opp["severity"] == "watch"
    assert opp["probe"] is not None and opp["probe"]["fit_delta_a"] > 0
    assert "doubtful" in opp["note"]
    assert all_notifications(engine) == []


# --- push budget: 2 per league-week, then degrade (spec §3) -----------------


def test_push_budget_exhaustion_degrades_third_to_watch(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    rivals = []
    for tid, tname in [(7, "Big Truss"), (8, "Rival B"), (9, "Rival C")]:
        roster = [
            (p if p[0] != 701 else (p[0], p[1], p[2], p[3], p[4], p[5]))
            for p in RIVAL_ROSTER
        ]
        # shift player ids so each rival has its own injured RB
        shifted = [(pid + tid * 1000, name, pos, rate, stat, slot)
                   for (pid, name, pos, rate, stat, slot) in roster]
        rivals.append((tid, tname, shifted))
    seed_league(engine, rivals=rivals)
    all_players = list(MY_ROSTER)
    for _, _, r in rivals:
        all_players += r
    seed_prior_state(engine, {pid: None for (pid, *_) in all_players})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    wins = windows(report)
    assert len(wins) == 2                     # budget of 2
    assert len(watches(report)) == 1
    suppressed = watches(report)[0]
    assert "budget" in suppressed["note"]
    assert len(all_notifications(engine)) == 2


# --- dedupe across re-scans: out -> Q -> out does not re-push (spec §6) ------


def test_status_oscillation_does_not_re_push(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + RIVAL_ROSTER})

    # scan 1: 701 out (was active) -> push
    asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))
    assert len(all_notifications(engine)) == 1

    # scan 2: 701 questionable -> no trigger, state advances
    asyncio.run(set_player_status(engine, 7, 701, "questionable"))
    asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))
    assert len(all_notifications(engine)) == 1

    # scan 3: 701 out again (was questionable) -> new event, but dedupe_key
    # has no week -> re-aggravation does not re-page
    asyncio.run(set_player_status(engine, 7, 701, "out"))
    asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))
    assert len(all_notifications(engine)) == 1


# --- my own player gets hurt: M is not a rival of itself (spec §6) -----------


def test_my_own_injured_player_does_not_trigger(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    my = [(p if p[0] != 301 else (301, "My RB1", "RB", 14.1, "out", "RB"))
          for p in MY_ROSTER]
    seed_league(engine, my_roster=my)
    seed_prior_state(engine, {pid: None for (pid, *_) in my + RIVAL_ROSTER})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    assert opp_for(report, 301) is None
    assert all_notifications(engine) == []


# --- ESPN_MY_TEAMS missing: report works, pushes never fire (spec §6) --------


def test_my_team_missing_caps_everything_at_watch(monkeypatch):
    monkeypatch.setattr("models.opportunity_scanner.ESPN_MY_TEAMS", {})
    engine = make_engine()
    seed_league(engine)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + RIVAL_ROSTER})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    assert report["my_team_id"] is None
    opp = opp_for(report, 701)
    assert opp is not None
    assert opp["severity"] == "watch"
    assert opp["my_surplus"] == []
    assert all_notifications(engine) == []


# --- D2 InjuryDesignation overrides ESPN status (optional-by-construction) ---


def test_injury_designation_overrides_espn_status(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    # ESPN roster says active, but D2 designation says out -> effective 'out'
    rival = [(7, "Big Truss", [
        (p if p[0] != 701 else (701, "J. Starter", "RB", 14.1, None, "RB"))
        for p in RIVAL_ROSTER
    ])]
    seed_league(engine, rivals=rival)

    async def seed_designation():
        await engine.save(
            InjuryDesignation(
                season=SEASON, week=W0, player_name="J. Starter",
                designation="out", nfl_team=None, position="RB",
            )
        )

    asyncio.run(seed_designation())
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + rival[0][2]})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    wins = windows(report)
    assert len(wins) == 1
    assert wins[0]["injured"]["status"] == "out"
    assert len(all_notifications(engine)) == 1


# --- two rivals lose players the same week: both push (spec §6) --------------


def test_two_rivals_same_week_both_push(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    rivals = []
    for tid, tname in [(7, "Big Truss"), (8, "Rival B")]:
        shifted = [(pid + tid * 1000, name, pos, rate, stat, slot)
                   for (pid, name, pos, rate, stat, slot) in RIVAL_ROSTER]
        rivals.append((tid, tname, shifted))
    seed_league(engine, rivals=rivals)
    all_players = list(MY_ROSTER)
    for _, _, r in rivals:
        all_players += r
    seed_prior_state(engine, {pid: None for (pid, *_) in all_players})

    report = asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))

    assert len(windows(report)) == 2
    assert len(all_notifications(engine)) == 2


# --- on-demand report: purity + zero triggers (spec §5, §6) -----------------


def test_report_is_pure_and_works_with_zero_triggers(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)

    # No prior state, no mutation: the report shows the ongoing window
    first = asyncio.run(trade_opportunity_report(engine, LEAGUE_ID, SEASON))
    second = asyncio.run(trade_opportunity_report(engine, LEAGUE_ID, SEASON))

    assert first == second                              # deterministic
    assert all_notifications(engine) == []              # never pushes
    assert all_state(engine) == []                      # never mutates state
    # 701 is currently out -> the report still surfaces the window
    assert len(windows(first)) == 1
    assert first["my_team_id"] == MY_TEAM


def test_report_never_consumes_push_budget(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + RIVAL_ROSTER})

    # Re-fetching the report many times leaves the push budget untouched
    for _ in range(5):
        asyncio.run(trade_opportunity_report(engine, LEAGUE_ID, SEASON))

    # A subsequent scan still pushes (budget was not consumed by reads)
    asyncio.run(scan_league(engine, LEAGUE_ID, SEASON))
    assert len(all_notifications(engine)) == 1


# --- cached-only: neither module imports data_sources -----------------------


def test_modules_never_import_data_sources():
    import ast
    import inspect

    import opportunity_api
    from models import opportunity_scanner

    for module in (opportunity_api, opportunity_scanner):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for name in imported:
                assert not name.startswith("data_sources"), (
                    f"{module.__name__} imports {name} — E4 must stay cached-only"
                )


# --- run_opportunity_scan: enabled gate + multi-league ----------------------


def test_run_opportunity_scan_disabled_by_default(monkeypatch):
    monkeypatch.setattr("models.opportunity_scanner.ESPN_MY_TEAMS", {})
    engine = make_engine()
    seed_league(engine)
    monkeypatch.setattr("models.opportunity_scanner.TRADE_SCAN_ENABLED", False)

    result = asyncio.run(run_opportunity_scan(engine, SEASON))
    assert result["enabled"] is False
    assert result["leagues"] == {}
    assert all_notifications(engine) == []


def test_run_opportunity_scan_runs_all_leagues_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)
    seed_prior_state(engine, {pid: None for (pid, *_) in MY_ROSTER + RIVAL_ROSTER})
    monkeypatch.setattr("models.opportunity_scanner.TRADE_SCAN_ENABLED", True)

    result = asyncio.run(run_opportunity_scan(engine, SEASON))
    assert result["enabled"] is True
    assert LEAGUE_ID in result["leagues"]
    assert len(windows(result["leagues"][LEAGUE_ID])) == 1


# --- HTTP endpoint: envelope + freshness ------------------------------------


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        "models.opportunity_scanner.ESPN_MY_TEAMS", {LEAGUE_ID: MY_TEAM}
    )
    engine = make_engine()
    seed_league(engine)
    app = FastAPI()
    app.include_router(opportunity_api.router)
    opportunity_api.configure(lambda: engine)
    with TestClient(app) as c:
        yield c, engine


def test_endpoint_returns_enveloped_report(client):
    c, engine = client
    resp = c.get(f"/inseason/league/{LEAGUE_ID}/trade_opportunities?season={SEASON}")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "data" in payload and "freshness" in payload and "warnings" in payload
    assert payload["data"]["my_team_id"] == MY_TEAM
    assert len(windows(payload["data"])) == 1


def test_endpoint_404_for_unsynced_league(client):
    c, _ = client
    resp = c.get(f"/inseason/league/999/trade_opportunities?season={SEASON}")
    assert resp.status_code == 404


def test_endpoint_does_not_mutate_state_or_push(client):
    c, engine = client
    for _ in range(3):
        c.get(f"/inseason/league/{LEAGUE_ID}/trade_opportunities?season={SEASON}")
    assert all_notifications(engine) == []
    assert all_state(engine) == []
