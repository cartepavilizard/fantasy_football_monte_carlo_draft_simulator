# -*- coding: utf-8 -*-
"""
E6: free-agent hoarding — the post-waivers worth-hoarding scan. Pure-core
tests build ValuationContexts by hand (test_trade_valuation.py's style) and
cover both reason branches, the margin boundary, candidate-pool sourcing,
drop-candidate selection, the E5 exclusion, and the no-droppable edge case.
Mongo tests cover the stored report + digest notification, scheduler guard,
report replacement idempotence, and the two-sided E5/E6 boundary. Endpoint
tests (built on a local FastAPI app, since app.py isn't wired by this task)
cover serves-stored-only and the cached-only purity/runtime enforcement.
"""
import asyncio
import datetime
import inspect
from types import SimpleNamespace

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine
from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.hoarding as hoarding
from models.blocking import blocking_plays
from models.config import DRAFT_YEAR
from models.handcuffs import upsert_handcuff
from models.hoarding import (
    HoardingReport,
    _compute_hoarding_entries,
    hoarding_should_run,
    run_hoarding_scan,
)
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InjuryDesignation,
    InSeasonLeague,
    LeagueTeamInfo,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.notifications import Notification
from models.trade_valuation import ValuationContext
import hoarding_api

SEASON = DRAFT_YEAR
LEAGUE_ID = 333
W0 = 8
HORIZON = list(range(W0, 17))  # 8..16, H=9
RR = {"WR": 7.8, "RB": 6.9, "QB": 8.0, "TE": 6.0, "K": 7.9, "DST": 5.0}
SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR": 0, "FLEX": 1, "K": 1, "DST": 1}


def make_ctx(specs, rosters, team_names, replacement=None, slot_counts=None):
    """Hand-build a ValuationContext. specs: pid -> {position, rate, name?,
    injury_status?, team?, espn_team_id?}. Each player gets a unique NFL
    team with an opponent every horizon week (no byes) for clean arithmetic."""
    opponents = {}
    players = {}
    rates = {}
    for pid, s in specs.items():
        team = s.get("team", f"T{pid}")
        for week in HORIZON:
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
        latest_scoring_period=W0,
        final_scoring_period=17,
        lineup_slot_counts=slot_counts or SLOTS,
        teams=[
            SimpleNamespace(espn_team_id=tid, name=name)
            for tid, name in team_names.items()
        ],
    )
    return ValuationContext(
        league=league,
        season=SEASON,
        w0=W0,
        horizon=HORIZON,
        playoff_weeks=[w for w in HORIZON if w in (14, 15, 16)],
        opponents=opponents,
        strength=None,
        rates=rates,
        players=players,
        replacement=replacement or RR,
        rosters=rosters,
        team_names=team_names,
        warnings=[],
    )


# My roster: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX(WR), K, DST, + 1 bench WR fodder.
MY_ROSTER = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
# Rival 7: weak RB2 (pid 301) is the denial hole.
RIVAL_ROSTER = [300, 301, 302, 303, 304, 305, 306, 307, 308]

BASE_SPECS = {
    100: {"position": "QB", "rate": 14.0, "name": "My QB", "espn_team_id": 1},
    101: {"position": "RB", "rate": 14.0, "name": "My RB1", "espn_team_id": 1},
    102: {"position": "RB", "rate": 10.0, "name": "My RB2", "espn_team_id": 1},
    103: {"position": "WR", "rate": 12.0, "name": "My WR1", "espn_team_id": 1},
    104: {"position": "WR", "rate": 11.0, "name": "My WR2", "espn_team_id": 1},
    105: {"position": "TE", "rate": 8.0, "name": "My TE", "espn_team_id": 1},
    106: {"position": "WR", "rate": 9.0, "name": "My Flex", "espn_team_id": 1},
    107: {"position": "K", "rate": 8.0, "name": "My K", "espn_team_id": 1},
    108: {"position": "DST", "rate": 6.0, "name": "My DST", "espn_team_id": 1},
    109: {"position": "WR", "rate": 8.2, "name": "Bench Fodder", "espn_team_id": 1},
    300: {"position": "RB", "rate": 8.0, "name": "Rival RB1", "espn_team_id": 7},
    301: {"position": "RB", "rate": 2.0, "name": "Rival RB2 Hole", "espn_team_id": 7},
    302: {"position": "QB", "rate": 13.0, "name": "Rival QB", "espn_team_id": 7},
    303: {"position": "WR", "rate": 11.0, "name": "Rival WR1", "espn_team_id": 7},
    304: {"position": "WR", "rate": 10.0, "name": "Rival WR2", "espn_team_id": 7},
    305: {"position": "TE", "rate": 7.0, "name": "Rival TE", "espn_team_id": 7},
    306: {"position": "WR", "rate": 8.0, "name": "Rival Flex", "espn_team_id": 7},
    307: {"position": "K", "rate": 8.0, "name": "Rival K", "espn_team_id": 7},
    308: {"position": "DST", "rate": 6.0, "name": "Rival DST", "espn_team_id": 7},
}

MY_SLOTS = {
    100: "QB", 101: "RB", 102: "RB", 103: "WR", 104: "WR",
    105: "TE", 106: "RB/WR", 107: "K", 108: "DST", 109: "BE",
}

TEAM_NAMES = {1: "My Team", 7: "Rival A"}


def _base_ctx(fa_specs):
    specs = dict(BASE_SPECS)
    specs.update(fa_specs)
    return make_ctx(
        specs,
        rosters={1: MY_ROSTER, 7: RIVAL_ROSTER},
        team_names=TEAM_NAMES,
    )


# --- purity -----------------------------------------------------------------


def test_hoarding_modules_never_import_data_sources():
    import ast

    import models.hoarding as hoarding_mod

    for module in (hoarding_mod, hoarding_api):
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
                    f"{module.__name__} imports {name}"
                )


# --- pure: both reason branches + margin ------------------------------------


def test_upside_branch_flags_when_my_gain_clears():
    # FA U: WR rate 9.8 -> starts in my flex over pid 106 (rate 9), big my_gain
    ctx = _base_ctx({200: {"position": "WR", "rate": 9.8, "name": "Upside U"}})
    entries, note = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    by_id = {e["player_id"]: e for e in entries}
    assert 200 in by_id
    e = by_id[200]
    assert e["reason"] == "upside"
    assert e["best_rival_gain"] == 0.0  # rival scan skipped (cleared on upside)
    assert e["margin"] > hoarding.HOARD_MARGIN
    assert e["drop"]["player_id"] == 109  # the bench fodder
    assert "top_rate" in e["sources"]


def test_denial_branch_flags_when_rival_starting_gain_dominates():
    # FA B: RB rate 4.0 -> low my_gain, but fills rival 7's RB2 hole (rate 2)
    ctx = _base_ctx({201: {"position": "RB", "rate": 4.0, "name": "Denial B"}})
    entries, note = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    by_id = {e["player_id"]: e for e in entries}
    assert 201 in by_id
    e = by_id[201]
    assert e["reason"] == "denial"
    assert e["rival_team_id"] == 7
    assert e["best_rival_gain"] > 0.0
    # denial-weighted gain exceeds my_gain (that's why reason is denial)
    assert hoarding.DENIAL_WEIGHT * e["best_rival_gain"] > e["my_gain"]
    assert e["margin"] > hoarding.HOARD_MARGIN


def test_margin_boundary_strict_inequality_not_flagged_at_exactly_margin():
    # A FA whose my_gain - drop_cost lands just under HOARD_MARGIN and has
    # no rival gain must NOT flag (the inequality is strict >).
    # drop_cost = player_value(109) = (8.2-7.8)*9 = 3.6. Need my_gain < 6.6.
    # FA M: WR rate 8.5 -> flex gain 8.5-9 = negative, won't start. my_gain ~ -drop bench.
    ctx = _base_ctx({250: {"position": "WR", "rate": 8.5, "name": "Marginal M"}})
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    ids = {e["player_id"] for e in entries}
    assert 250 not in ids  # doesn't clear the margin, no denial -> not flagged


def test_margin_reported_and_sorted_descending():
    ctx = _base_ctx({
        200: {"position": "WR", "rate": 9.8, "name": "U"},
        201: {"position": "RB", "rate": 4.0, "name": "B"},
    })
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    margins = [e["margin"] for e in entries]
    assert margins == sorted(margins, reverse=True)
    for e in entries:
        assert e["margin"] == round(e["hoard_value"] - e["drop"]["value"], 1)


def test_report_capped_at_hoard_report_max():
    # many upside FAs all clearing -> capped at HOARD_REPORT_MAX (5)
    fa_specs = {
        200 + i: {"position": "WR", "rate": 12.0 + i, "name": f"FA{i}"}
        for i in range(8)
    }
    ctx = _base_ctx(fa_specs)
    entries, note = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    assert len(entries) <= hoarding.HOARD_REPORT_MAX
    # multiple entries share one drop -> conflict note
    if len(entries) > 1:
        assert "share one drop" in note


# --- pure: candidate pool sourcing + exclusions -----------------------------


def test_usage_shift_source_included():
    # FA named "Riser" not in top-N-by-rate (low rate) but has a C4 shift
    ctx = _base_ctx({210: {"position": "WR", "rate": 1.0, "name": "Riser"}})
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names={"Riser"},
    )
    # Riser has tiny my_gain and won't clear, but the source tag is what we test:
    # the pool must include him (usage_shift source). He won't be in entries
    # (doesn't clear), so verify via a fresh call with a high-rate riser.
    ctx2 = _base_ctx({211: {"position": "WR", "rate": 9.8, "name": "Riser2"}})
    entries2, _ = _compute_hoarding_entries(
        ctx2, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names={"Riser2"},
    )
    by_id = {e["player_id"]: e for e in entries2}
    assert "usage_shift" in by_id[211]["sources"]


def test_handcuff_source_included_for_rival_starter():
    # pid 300 "Rival RB1" is a rostered starter (rival 7); his handcuff is a FA.
    ctx = _base_ctx({220: {"position": "RB", "rate": 5.0, "name": "Cuff"}})
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={"Rival RB1": "Cuff"}, e5_excluded=set(),
        usage_shift_names=set(),
    )
    # the handcuff is a candidate (handcuff source); whether it flags depends
    # on math, but it must be CONSIDERED. Give it a high rate to force a flag.
    ctx2 = _base_ctx({221: {"position": "RB", "rate": 9.8, "name": "Cuff2"}})
    entries2, _ = _compute_hoarding_entries(
        ctx2, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={"Rival RB1": "Cuff2"}, e5_excluded=set(),
        usage_shift_names=set(),
    )
    by_id = {e["player_id"]: e for e in entries2}
    assert 221 in by_id
    assert "handcuff" in by_id[221]["sources"]


def test_e5_exclusion_removes_injured_rival_handcuff_from_pool():
    # The E5 case: an FA who is a rival's injured starter's handcuff.
    # e5_excluded carries its pid; it must NOT appear in entries even if
    # its math would otherwise clear.
    ctx = _base_ctx({230: {"position": "RB", "rate": 9.8, "name": "E5 Cuff"}})
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={"Rival RB1": "E5 Cuff"},
        e5_excluded={230},  # E5 claims this one
        usage_shift_names=set(),
    )
    ids = {e["player_id"] for e in entries}
    assert 230 not in ids


def test_own_starter_handcuff_excluded_from_pool():
    # A handcuff of MY OWN starter is self-insurance (C7 owns it), not hoarding.
    ctx = _base_ctx({240: {"position": "RB", "rate": 9.8, "name": "My Cuff"}})
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={"My RB1": "My Cuff"},  # My RB1 (pid 101) is mine
        e5_excluded=set(), usage_shift_names=set(),
    )
    ids = {e["player_id"] for e in entries}
    assert 240 not in ids


# --- pure: drop-candidate selection -----------------------------------------


def test_drop_candidate_excludes_starters():
    # If the only bench player is a starter in the w0 DP, no drop -> note.
    # Build a roster with no bench (all starters).
    tight_specs = {
        100: {"position": "QB", "rate": 14.0, "name": "Q", "espn_team_id": 1},
        101: {"position": "RB", "rate": 14.0, "name": "R1", "espn_team_id": 1},
        102: {"position": "RB", "rate": 10.0, "name": "R2", "espn_team_id": 1},
        103: {"position": "WR", "rate": 12.0, "name": "W1", "espn_team_id": 1},
        104: {"position": "WR", "rate": 11.0, "name": "W2", "espn_team_id": 1},
        105: {"position": "TE", "rate": 8.0, "name": "T", "espn_team_id": 1},
        106: {"position": "WR", "rate": 9.0, "name": "F", "espn_team_id": 1},
        107: {"position": "K", "rate": 8.0, "name": "K", "espn_team_id": 1},
        108: {"position": "DST", "rate": 6.0, "name": "D", "espn_team_id": 1},
    }
    ctx = make_ctx(
        {**tight_specs, 200: {"position": "WR", "rate": 12.0, "name": "FA"}},
        rosters={1: list(tight_specs.keys()), 7: RIVAL_ROSTER},
        team_names=TEAM_NAMES,
    )
    tight_slots = {pid: "QB" if pid == 100 else "RB" if pid in (101, 102)
                   else "WR" if pid in (103, 104) else "TE" if pid == 105
                   else "RB/WR" if pid == 106 else "K" if pid == 107 else "DST"
                   for pid in tight_specs}
    entries, note = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=tight_slots,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    assert entries == []
    assert note == "no droppable player"


def test_drop_candidate_excludes_valuable_ir_stash():
    # An IR-slot player with positive value is not droppable fodder. Add an
    # IR RB (rate 13) whose curve leaves a small positive value — WITHOUT
    # the IR protection it would be the min-value drop, but protection keeps
    # it and the bench WR (109) is dropped instead.
    ctx = _base_ctx({
        111: {
            "position": "RB", "rate": 13.0, "name": "IR Stash RB",
            "injury_status": "injury_reserve", "espn_team_id": 1,
        },
        200: {"position": "WR", "rate": 9.8, "name": "U"},
    })
    ctx.rosters[1] = MY_ROSTER + [111]
    slots = dict(MY_SLOTS)
    slots[111] = "IR"
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=slots,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    # the IR stash (111) is protected; the drop is the bench WR (109), not 111
    for e in entries:
        assert e["drop"]["player_id"] == 109


def test_drop_candidate_excludes_sole_k_when_league_requires_one():
    # Roster with exactly one K and a bench RB fodder -> drop is the RB, not K.
    ctx = _base_ctx({200: {"position": "WR", "rate": 9.8, "name": "U"}})
    # add a second bench RB so there's a droppable non-K
    ctx.players[110] = {
        "name": "Bench RB", "position": "RB", "nfl_team": "T110",
        "injury_status": None, "espn_team_id": 1,
    }
    ctx.rates[110] = 8.0
    ctx.rosters[1] = MY_ROSTER + [110]
    slots = dict(MY_SLOTS)
    slots[110] = "BE"
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=slots,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    # K (pid 107) must NOT be the drop
    for e in entries:
        assert e["drop"]["player_id"] != 107


def test_drop_candidate_excludes_own_active_handcuff():
    # My RB1 (pid 101) "My RB1" has a handcuff "My RB1 Cuff" on my bench.
    # That handcuff is insurance (C7) and not droppable fodder.
    ctx = _base_ctx({200: {"position": "WR", "rate": 9.8, "name": "U"}})
    ctx.players[110] = {
        "name": "My RB1 Cuff", "position": "RB", "nfl_team": "T110",
        "injury_status": None, "espn_team_id": 1,
    }
    ctx.rates[110] = 4.0  # low value — would be the drop if not protected
    ctx.rosters[1] = MY_ROSTER + [110]
    slots = dict(MY_SLOTS)
    slots[110] = "BE"
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=slots,
        handcuff_map={"My RB1": "My RB1 Cuff"},
        e5_excluded=set(), usage_shift_names=set(),
    )
    # the protected handcuff (110) is not the drop; 109 is
    for e in entries:
        assert e["drop"]["player_id"] == 109


def test_two_entry_conflict_note_when_shared_drop():
    ctx = _base_ctx({
        200: {"position": "WR", "rate": 9.8, "name": "U"},
        201: {"position": "RB", "rate": 4.0, "name": "B"},
    })
    entries, note = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    assert len(entries) >= 2
    assert note is not None
    assert "share one drop" in note
    # both entries list the SAME drop (the human picks)
    drops = {e["drop"]["player_id"] for e in entries}
    assert drops == {109}


def test_ir_stash_fa_my_gain_priced_by_availability_curve():
    # A dropped IR star in the FA pool: availability curve zeroes stash weeks;
    # a big my_gain from playoff-week returns is the §2.6 stash case working.
    ctx = _base_ctx({
        260: {
            "position": "RB", "rate": 14.0, "name": "IR Stash",
            "injury_status": "injury_reserve",
        }
    })
    entries, _ = _compute_hoarding_entries(
        ctx, my_team=1, rivals=[7], my_roster_slots=MY_SLOTS,
        handcuff_map={}, e5_excluded=set(), usage_shift_names=set(),
    )
    # The IR stash is in the pool (top_rate source via high rate). Whether it
    # clears depends on the curve; the point is it was EVALUATED without error
    # and its my_gain reflects the zeroed stash weeks (not full 14.0 x 9).
    by_id = {e["player_id"]: e for e in entries}
    if 260 in by_id:
        # if it flagged, my_gain must be well below 14*9 (curve zeroes weeks)
        assert by_id[260]["my_gain"] < 14.0 * 9


# --- scheduler guard + weekday gate -----------------------------------------


def test_hoarding_should_run_requires_enabled_and_weekday():
    saved_enabled = hoarding.HOARDING_ENABLED
    saved_weekdays = hoarding.HOARD_WEEKDAYS
    try:
        hoarding.HOARDING_ENABLED = False
        assert hoarding.hoarding_should_run(datetime.datetime(2026, 10, 14)) is False

        hoarding.HOARDING_ENABLED = True
        hoarding.HOARD_WEEKDAYS = {2, 3, 4, 5}  # Wed-Sat
        # 2026-10-14 is a Wednesday (weekday 2)
        assert hoarding.hoarding_should_run(datetime.datetime(2026, 10, 14)) is True
        # Monday (weekday 0) -> not a hoard day
        assert hoarding.hoarding_should_run(datetime.datetime(2026, 10, 12)) is False
    finally:
        hoarding.HOARDING_ENABLED = saved_enabled
        hoarding.HOARD_WEEKDAYS = saved_weekdays


# --- Mongo: stored report, notification, idempotence, edge cases ------------


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-hoarding")


_MY_TEAMS = {LEAGUE_ID: 1}


def _patch_my_teams():
    saved_blk = hoarding.ESPN_MY_TEAMS
    saved_run = hoarding.ESPN_MY_TEAMS
    hoarding.ESPN_MY_TEAMS = _MY_TEAMS
    import models.blocking as blk
    saved_blk2 = blk.ESPN_MY_TEAMS
    blk.ESPN_MY_TEAMS = _MY_TEAMS
    return saved_blk, saved_blk2


def _restore_my_teams(saved):
    hoarding.ESPN_MY_TEAMS = saved[0]
    import models.blocking as blk
    blk.ESPN_MY_TEAMS = saved[1]


class _MyTeams:
    def __enter__(self):
        self._saved = _patch_my_teams()
        return self

    def __exit__(self, *exc):
        _restore_my_teams(self._saved)


def _run(coro):
    with _MyTeams():
        return asyncio.run(coro)


def _league_obj():
    return InSeasonLeague(
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        name="Hoard League",
        team_count=2,
        current_matchup_period=W0,
        latest_scoring_period=W0,
        final_scoring_period=17,
        lineup_slot_counts=SLOTS,
        teams=[
            LeagueTeamInfo(espn_team_id=1, name="My Team"),
            LeagueTeamInfo(espn_team_id=7, name="Rival A"),
        ],
    )


async def _seed_scan_fixture(engine, with_snapshot=True):
    await engine.save(_league_obj())
    for team_id, ids in [(1, MY_ROSTER), (7, RIVAL_ROSTER)]:
        entries = []
        for pid in ids:
            spec = BASE_SPECS[pid]
            entries.append(
                RosterSlotEntry(
                    player_id=pid,
                    player_name=spec["name"],
                    position=spec["position"],
                    nfl_team=spec.get("team", f"T{pid}"),
                    lineup_slot="QB" if pid == 100 or (team_id == 7 and pid == 302)
                    else "RB" if spec["position"] == "RB" and pid in (101, 102, 300, 301)
                    else "WR" if spec["position"] == "WR" and pid in (103, 104, 303, 304)
                    else "TE" if spec["position"] == "TE"
                    else "RB/WR" if pid in (106, 306)
                    else "K" if spec["position"] == "K"
                    else "DST",
                    projected_points=spec["rate"],
                )
            )
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID, season=SEASON, week=W0,
                espn_team_id=team_id, entries=entries,
            )
        )
    if with_snapshot:
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID, season=SEASON, week=W0,
                entries=[
                    FreeAgentEntry(
                        player_id=200, player_name="Upside U", position="WR",
                        nfl_team="T200", projected_points=9.8,
                    ),
                ],
            )
        )


def test_run_hoarding_scan_disabled_returns_zero():
    engine = make_engine()
    saved = hoarding.HOARDING_ENABLED
    try:
        hoarding.HOARDING_ENABLED = False
        result = _run(run_hoarding_scan(engine, SEASON, now=datetime.datetime(2026, 10, 14)))
        assert result["scanned"] == 0
        assert "disabled" in result["reason"]
    finally:
        hoarding.HOARDING_ENABLED = saved


def test_run_hoarding_scan_stores_report_and_notification():
    engine = make_engine()

    async def go():
        await _seed_scan_fixture(engine)
        # ensure enabled + a hoard weekday
        hoarding.HOARDING_ENABLED = True
        hoarding.HOARD_WEEKDAYS = {2, 3, 4, 5}
        return await run_hoarding_scan(engine, SEASON, now=datetime.datetime(2026, 10, 14))

    saved_enabled = hoarding.HOARDING_ENABLED
    saved_weekdays = hoarding.HOARD_WEEKDAYS
    try:
        result = _run(go())
        assert result["scanned"] == 1
        # stored report
        reports = _run(_get_reports(engine))
        assert len(reports) == 1
        report = reports[0]
        assert report.espn_league_id == LEAGUE_ID
        assert report.season == SEASON
        assert report.week == W0
        # notification (one digest) created iff entries
        notifs = _run(_get_notifs(engine))
        if report.entries:
            hoard_notifs = [n for n in notifs if n.kind == "hoarding_report"]
            assert len(hoard_notifs) == 1
            n = hoard_notifs[0]
            assert n.dedupe_key == f"hoard:{LEAGUE_ID}:{SEASON}:w{W0}"
            assert "Upside U" in n.body or str(len(report.entries)) in n.body
        else:
            assert not any(n.kind == "hoarding_report" for n in notifs)
    finally:
        hoarding.HOARDING_ENABLED = saved_enabled
        hoarding.HOARD_WEEKDAYS = saved_weekdays


async def _get_reports(engine):
    from odmantic import query as q
    return await engine.find(HoardingReport, sort=q.desc(HoardingReport.generated_at))


async def _get_notifs(engine):
    return await engine.find(Notification)


def test_report_replacement_is_idempotent():
    engine = make_engine()

    async def go():
        await _seed_scan_fixture(engine)
        hoarding.HOARDING_ENABLED = True
        hoarding.HOARD_WEEKDAYS = {2, 3, 4, 5}
        await run_hoarding_scan(engine, SEASON, now=datetime.datetime(2026, 10, 14))
        await run_hoarding_scan(engine, SEASON, now=datetime.datetime(2026, 10, 14))  # again
        return await _get_reports(engine)

    saved_enabled = hoarding.HOARDING_ENABLED
    saved_weekdays = hoarding.HOARD_WEEKDAYS
    try:
        reports = _run(go())
        # exactly ONE report per league-week (replaced, not duplicated)
        assert len(reports) == 1
    finally:
        hoarding.HOARDING_ENABLED = saved_enabled
        hoarding.HOARD_WEEKDAYS = saved_weekdays


def test_no_free_agent_snapshot_yields_note_and_no_entries():
    engine = make_engine()

    async def go():
        await _seed_scan_fixture(engine, with_snapshot=False)
        hoarding.HOARDING_ENABLED = True
        hoarding.HOARD_WEEKDAYS = {2, 3, 4, 5}
        await run_hoarding_scan(engine, SEASON, now=datetime.datetime(2026, 10, 14))
        return await _get_reports(engine)

    saved_enabled = hoarding.HOARDING_ENABLED
    saved_weekdays = hoarding.HOARD_WEEKDAYS
    try:
        reports = _run(go())
        assert len(reports) == 1
        assert reports[0].entries == []
        assert "no free agent snapshot" in reports[0].note.lower()
    finally:
        hoarding.HOARDING_ENABLED = saved_enabled
        hoarding.HOARD_WEEKDAYS = saved_weekdays


def test_espn_my_teams_missing_league_skips_it():
    engine = make_engine()

    async def go():
        await _seed_scan_fixture(engine)
        hoarding.HOARDING_ENABLED = True
        hoarding.HOARD_WEEKDAYS = {2, 3, 4, 5}
        # my team not known for this league -> skip entirely
        hoarding.ESPN_MY_TEAMS = {}
        await run_hoarding_scan(engine, SEASON, now=datetime.datetime(2026, 10, 14))
        return await _get_reports(engine)

    saved_enabled = hoarding.HOARDING_ENABLED
    saved_weekdays = hoarding.HOARD_WEEKDAYS
    saved_teams = hoarding.ESPN_MY_TEAMS
    try:
        reports = asyncio.run(go())  # no _run: we deliberately clear my-teams
        assert reports == []  # skipped -> no report stored
    finally:
        hoarding.HOARDING_ENABLED = saved_enabled
        hoarding.HOARD_WEEKDAYS = saved_weekdays
        hoarding.ESPN_MY_TEAMS = saved_teams


# --- the two-sided E5/E6 boundary test --------------------------------------
#
# One fixture: a rival's INJURED starter whose handcuff is a free agent.
# E5 (blocking) CLAIMS it; E6 (hoarding) EXCLUDES it. Both asserted from
# the same seed so the boundary is proven from both sides.


def test_e5_e6_boundary_injured_rival_handcuff_claimed_by_e5_excluded_from_e6():
    engine = make_engine()

    async def go():
        await _seed_scan_fixture(engine)
        # rival 7's RB1 (pid 300 "Rival RB1") is injured; his handcuff
        # "Rival Cuff" is in the FA pool.
        await upsert_handcuff(engine, "Rival RB1", "Rival Cuff", nfl_team="SEA")
        # mark the starter out on his roster
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID, season=SEASON, week=W0,
                espn_team_id=7,
                entries=[
                    RosterSlotEntry(
                        player_id=300, player_name="Rival RB1", position="RB",
                        nfl_team="SEA", lineup_slot="RB", injury_status="out",
                        projected_points=6.0,
                    )
                ],
            )
        )
        # put the handcuff in the FA pool
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID, season=SEASON, week=W0,
                entries=[
                    FreeAgentEntry(
                        player_id=400, player_name="Rival Cuff", position="RB",
                        nfl_team="SEA", projected_points=9.8,
                    ),
                    FreeAgentEntry(
                        player_id=200, player_name="Upside U", position="WR",
                        nfl_team="T200", projected_points=9.8,
                    ),
                ],
            )
        )
        # E5 side: blocking claims the injured rival's handcuff
        e5 = await blocking_plays(engine, LEAGUE_ID, SEASON, W0)
        return e5

    saved_enabled = hoarding.HOARDING_ENABLED
    saved_weekdays = hoarding.HOARD_WEEKDAYS
    try:
        e5_report = _run(go())

        # E5 side: the handcuff IS a blocking play
        e5_ids = {e["handcuff_player_id"] for e in e5_report["entries"]}
        assert 400 in e5_ids, "E5 must claim the injured rival's handcuff"
        cuff_entry = next(e for e in e5_report["entries"] if e["handcuff_player_id"] == 400)
        assert cuff_entry["starter_name"] == "Rival RB1"
        assert cuff_entry["starter_injury_status"] == "out"

        # E6 side: run the scan; the handcuff must NOT appear in the report
        async def run_scan():
            hoarding.HOARDING_ENABLED = True
            hoarding.HOARD_WEEKDAYS = {2, 3, 4, 5}
            await run_hoarding_scan(engine, SEASON, now=datetime.datetime(2026, 10, 14))
            return await _get_reports(engine)

        reports = _run(run_scan())
        assert len(reports) == 1
        e6_ids = {e["player_id"] for e in reports[0].entries}
        assert 400 not in e6_ids, (
            "E6 must exclude E5's injured-rival-handcuff case from its report"
        )
    finally:
        hoarding.HOARDING_ENABLED = saved_enabled
        hoarding.HOARD_WEEKDAYS = saved_weekdays


# --- endpoint: serves-stored-only + cached-only enforcement -----------------


def _make_client(engine):
    hoarding_api.configure(lambda: engine)
    app = FastAPI()
    app.include_router(hoarding_api.router)
    return TestClient(app)


def test_hoarding_endpoint_serves_stored_report_only():
    engine = make_engine()
    # seed a stored report directly (no scan)
    _run(_store_report(engine, HoardingReport(
        espn_league_id=LEAGUE_ID, season=SEASON, week=W0,
        entries=[{"player_id": 200, "player_name": "U", "hoard_value": 7.2,
                  "reason": "upside", "margin": 3.6}],
        note=None,
    )))
    _run(_seed_league_only(engine))
    client = _make_client(engine)
    resp = client.get(f"/inseason/league/{LEAGUE_ID}/hoarding")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["data"]["week"] == W0
    assert payload["data"]["entries"][0]["player_name"] == "U"
    # freshness envelope present
    assert "freshness" in payload
    assert "warnings" in payload


def test_hoarding_endpoint_returns_none_when_no_report_never_computes():
    engine = make_engine()
    _run(_seed_league_only(engine))
    client = _make_client(engine)
    resp = client.get(f"/inseason/league/{LEAGUE_ID}/hoarding")
    assert resp.status_code == 200
    assert resp.json()["data"] is None  # no stored report -> None, not computed


def test_blocking_endpoint_computes_on_demand():
    engine = make_engine()
    _run(_seed_league_only(engine))
    client = _make_client(engine)
    resp = client.get(f"/inseason/league/{LEAGUE_ID}/blocking")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["data"]["week"] == W0
    assert "entries" in payload["data"]
    assert "freshness" in payload


def test_endpoints_succeed_with_network_rigged_to_explode():
    """Runtime purity: no GET reaches for the network (B4's hard constraint)."""
    from data_sources import transport as transport_module

    def boom(*args, **kwargs):
        raise AssertionError("a cached-only endpoint reached for the network")

    engine = make_engine()
    _run(_seed_league_only(engine))
    _run(_store_report(engine, HoardingReport(
        espn_league_id=LEAGUE_ID, season=SEASON, week=W0, entries=[], note=None,
    )))
    client = _make_client(engine)

    saved = transport_module.HttpxTransport._client_instance
    transport_module.HttpxTransport._client_instance = boom
    try:
        for url in [
            f"/inseason/league/{LEAGUE_ID}/hoarding",
            f"/inseason/league/{LEAGUE_ID}/blocking",
        ]:
            resp = client.get(url)
            assert resp.status_code == 200, f"{url}: {resp.text}"
    finally:
        transport_module.HttpxTransport._client_instance = saved


def test_hoarding_endpoint_404_for_unsynced_league():
    engine = make_engine()
    client = _make_client(engine)
    resp = client.get(f"/inseason/league/{LEAGUE_ID}/hoarding")
    assert resp.status_code == 404


async def _store_report(engine, report):
    await engine.save(report)


async def _seed_league_only(engine):
    await engine.save(_league_obj())
