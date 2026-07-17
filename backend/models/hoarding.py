# -*- coding: utf-8 -*-
"""
FREE-AGENT HOARDING (PHASE E, TASK E6 — the post-waivers worth-hoarding scan)

After waivers process (typically Wednesday morning), every remaining free
agent is claimable first-come-first-served until Sunday. Hoarding is
spending a bench spot NOW on a player you don't strictly need, because the
option is about to disappear — either the player breaks out (speculative
upside for you) or a rival needs them this week (denial). The cost is
whoever you drop.

THE DEFINITION — one inequality (spec §2, implemented verbatim):

    hoard_value(f) - drop_cost > HOARD_MARGIN        (default 3.0 ROS pts)

with all three quantities in E1 units, computed on one shared
ValuationContext per league. hoard_value is the BETTER of two reasons to
hold them:

    hoard_value(f) = max( my_gain(f), DENIAL_WEIGHT * best_rival_gain(f) )

- my_gain      = E1 fit_delta of adding f to my roster (dropping the drop
  candidate) — the speculative-upside lens, via the full-horizon team DP.
- best_rival_gain = max over rival teams R of the STARTING-lineup gain of
  R adding f (dropping R's min-value player), restricted to rivals for
  whom f fills a starting hole (denial only matters when they'd play them).
- DENIAL_WEIGHT (0.5): denying a rival a point is worth less than scoring
  one yourself. max, not sum — one bench slot can't realize both stories
  at full weight (spec §2.1).

drop_cost is my roster's minimum-player_value player, excluding current
starters (w0 DP), valuable IR stashes, my own starters' active handcuffs
(C7 insurance is not fodder), and a sole K/DST when the league requires
starting them (spec §2.2). Floored at 0; if no legal drop exists, the
roster is full in the real sense — report "no droppable player", flag
nothing.

THE BOUNDARY (spec §1): E6 EXCLUDES E5's cases — injured rival starters'
handcuffs — from its candidate pool, rather than double-flagging them.
This module imports rival_injured_star_handcuff_ids() from models/blocking
so the boundary lives in one place and is testable from both sides. Also
excluded per §1: K/DST streaming (C3) and handcuffing your own starters
(C7) — the latter because a pure self-insurance add is C7's flag, not a
hoarding action.

CANDIDATE POOL (spec §2.3, bounded): top HOARD_POOL_TOP_N FAs by E1 rate,
plus any FA with a C4 rising-usage shift this week, plus any FA who is a
C7 handcuff of any rostered starter league-wide (minus E5's injured-star
cases and minus my own starters' handcuffs). my_gain is computed for all;
best_rival_gain only for those within reach (my_gain - drop_cost > 0) or
the top HOARD_RIVAL_SCAN_N by rate — bounding the expensive rival DP.

OUTPUT: a stored HoardingReport (replaced per league-week) plus ONE digest
notification per league-week via ensure_notification (kind
"hoarding_report"). Individual entries never push separately — this is a
weekly digest, not an alert stream (E4 owns interrupting). The GET
endpoint serves the stored report and NEVER computes; the scan runs only
via run_hoarding_scan() (scheduler wiring) or POST /inseason/sync's
pipeline.

TUNABLES: every constant is an env-overridable os.getenv in THIS module
(config.py is not edited — it is outside this task's boundary). Defaults
match spec §3.
"""
import datetime
import os
from typing import Dict, List, Optional, Set, Tuple

from odmantic import Model
from odmantic import Field as ODField
from odmantic import query

from .blocking import rival_injured_star_handcuff_ids
from .config import ESPN_MY_TEAMS
from .handcuffs import list_handcuffs
from .inseason import FreeAgentSnapshot, InSeasonLeague, TeamWeekRoster
from .lineup import best_assignment, slot_instances
from .notifications import ensure_notification
from .trade_valuation import (
    ValuationContext,
    build_context,
    expected_points,
    player_value,
    team_ros_points,
)
from .usage_shifts import detect_usage_shifts

# --- tunables (env, never edit models/config.py) -----------------------------

HOARD_MARGIN = float(os.getenv("HOARD_MARGIN", "3.0"))
DENIAL_WEIGHT = float(os.getenv("DENIAL_WEIGHT", "0.5"))
HOARD_POOL_TOP_N = int(os.getenv("HOARD_POOL_TOP_N", "20"))
HOARD_RIVAL_SCAN_N = int(os.getenv("HOARD_RIVAL_SCAN_N", "10"))
HOARD_REPORT_MAX = int(os.getenv("HOARD_REPORT_MAX", "5"))
HOARDING_ENABLED = os.getenv("HOARDING_ENABLED", "false").lower() == "true"
# Wed-Sat (Mon=0): after waivers, before Sunday. Mon/Tuesday's post-waiver
# state is stale and the report should not exist then (spec §3).
HOARD_WEEKDAYS = {
    int(d) for d in os.getenv("HOARD_WEEKDAYS", "2,3,4,5").split(",") if d
}


class HoardingReport(Model):
    """The stored weekly post-waivers report — replaced per league-week
    like every sync scope (spec §3). One document per (league, season,
    week); the GET endpoint serves whatever exists with its generated_at."""

    model_config = {"collection": "hoarding_reports"}

    espn_league_id: int
    season: int
    week: int
    generated_at: datetime.datetime = ODField(
        default_factory=datetime.datetime.now
    )
    entries: List[dict] = []
    note: Optional[str] = None


# --- scheduler guard ----------------------------------------------------------


def hoarding_should_run(now: Optional[datetime.datetime] = None) -> bool:
    """The scheduler gate: HOARDING_ENABLED env AND a HOARD_WEEKDAYS day.
    The orchestrator calls run_hoarding_scan(); this gate keeps the scan
    off on disabled configs and on Mon/Tue when post-waiver state is stale."""
    if not HOARDING_ENABLED:
        return False
    now = now or datetime.datetime.now()
    return now.weekday() in HOARD_WEEKDAYS


# --- pure helpers (no Mongo) --------------------------------------------------
# These take a built ValuationContext plus the extra inputs build_context
# doesn't carry (my roster's lineup slots, the C7 map, E5's exclusion set,
# C4 rising names). Pure so they unit-test with hand-built contexts, like
# test_trade_valuation does.


def _starting_ros_points(ctx: ValuationContext, player_ids: List[int]) -> float:
    """Starting-lineup ROS points only (no bench term) — for rival denial
    gain, which only counts when f would actually START for them (spec
    §2.1: 'only count the rival's starting(w) improvement, not their bench
    term'). Mirrors team_ros_points minus the BENCH_FACTOR bench depth."""
    slots = slot_instances(ctx.league.lineup_slot_counts)
    candidates = [
        (pid, ctx.players[pid].get("position")) for pid in player_ids
    ]
    total = 0.0
    for week in ctx.horizon:
        weights = {
            pid: expected_points(ctx, pid, week) for pid in player_ids
        }
        _, starting = best_assignment(slots, candidates, weights)
        total += starting
    return total


def _w0_starters(ctx: ValuationContext, roster_ids: List[int]) -> Set[int]:
    """The live week's optimal lineup (one E1 week-w0 DP) — these are not
    droppable (spec §2.2)."""
    if not roster_ids:
        return set()
    slots = slot_instances(ctx.league.lineup_slot_counts)
    candidates = [
        (pid, ctx.players[pid].get("position")) for pid in roster_ids
    ]
    weights = {pid: expected_points(ctx, pid, ctx.w0) for pid in roster_ids}
    assignment, _ = best_assignment(slots, candidates, weights)
    return set(assignment.values())


def _drop_candidate(
    ctx: ValuationContext,
    my_team: int,
    my_roster_slots: Dict[int, str],
    handcuff_map: Dict[str, str],
) -> Optional[Tuple[int, float]]:
    """My roster's minimum-player_value player, excluding current starters,
    valuable IR stashes, my own starters' active handcuffs, and a sole
    K/DST when the league requires starting them (spec §2.2). Returns
    (player_id, value_floored_at_0) or None when no legal drop exists
    (roster full in the real sense -> flag nothing, note the reason)."""
    roster_ids = list(ctx.rosters.get(my_team, []))
    if not roster_ids:
        return None
    starters = _w0_starters(ctx, roster_ids)

    # my own starters' handcuffs that are also on my roster — insurance,
    # not fodder (C7 owns them; dropping them breaks the insurance map)
    my_names_to_pid = {
        ctx.players[pid]["name"]: pid for pid in roster_ids
    }
    own_handcuff_ids: Set[int] = set()
    for starter_name, handcuff_name in handcuff_map.items():
        if starter_name in my_names_to_pid and handcuff_name in my_names_to_pid:
            own_handcuff_ids.add(my_names_to_pid[handcuff_name])

    slot_counts = ctx.league.lineup_slot_counts
    requires_k = slot_counts.get("K", 0) > 0
    requires_dst = slot_counts.get("DST", 0) > 0
    my_k = [pid for pid in roster_ids if ctx.players[pid].get("position") == "K"]
    my_dst = [
        pid for pid in roster_ids if ctx.players[pid].get("position") == "DST"
    ]

    droppable: List[int] = []
    for pid in roster_ids:
        if pid in starters:
            continue
        slot = my_roster_slots.get(pid, "BE")
        if slot == "IR":
            # IR-slot player with positive value is a valuable stash, not fodder
            if player_value(ctx, pid)["value"] > 0:
                continue
        if pid in own_handcuff_ids:
            continue
        pos = ctx.players[pid].get("position")
        if pos == "K" and requires_k and len(my_k) == 1:
            continue
        if pos == "DST" and requires_dst and len(my_dst) == 1:
            continue
        droppable.append(pid)

    if not droppable:
        return None
    drop_id = min(droppable, key=lambda pid: player_value(ctx, pid)["value"])
    drop_value = max(player_value(ctx, drop_id)["value"], 0.0)
    return drop_id, drop_value


def _my_gain(ctx: ValuationContext, my_team: int, drop_id: int, fa_id: int) -> float:
    """E1 fit_delta of adding fa (dropping the drop candidate) — the
    speculative-upside lens, via the full-horizon team DP (bench depth
    included). Never floored (a drop can hurt, though it rarely does here)."""
    before = list(ctx.rosters.get(my_team, []))
    after = [p for p in before if p != drop_id] + [fa_id]
    return team_ros_points(ctx, after) - team_ros_points(ctx, before)


def _rival_starting_gain(
    ctx: ValuationContext, rival_ids: List[int], fa_id: int
) -> float:
    """The STARTING-lineup ROS-point gain for a rival adding f (dropping
    their min-value player). Restricted to positive gains only — denial
    only matters when f fills a starting hole for them (spec §2.1)."""
    if not rival_ids:
        return 0.0
    drop_id = min(rival_ids, key=lambda pid: player_value(ctx, pid)["value"])
    after = [p for p in rival_ids if p != drop_id] + [fa_id]
    gain = _starting_ros_points(ctx, after) - _starting_ros_points(ctx, rival_ids)
    return max(gain, 0.0)


def _best_rival_gain(
    ctx: ValuationContext, rivals: List[int], fa_id: int
) -> Tuple[float, Optional[int]]:
    """Max over rival teams of the starting-lineup gain, with the rival's
    team id attached (for the report). Returns (gain, rival_team_id); both
    are 0/None when no rival would start f."""
    best_gain = 0.0
    best_team: Optional[int] = None
    for rival in rivals:
        rival_ids = ctx.rosters.get(rival, [])
        if not rival_ids:
            continue
        gain = _rival_starting_gain(ctx, rival_ids, fa_id)
        if gain > best_gain:
            best_gain = gain
            best_team = rival
    return best_gain, best_team


def _candidate_pool(
    ctx: ValuationContext,
    handcuff_map: Dict[str, str],
    usage_shift_names: Set[str],
    e5_excluded: Set[int],
    my_team: int,
) -> Tuple[List[int], Dict[int, List[str]]]:
    """The bounded candidate pool (spec §2.3): top-N by rate UNION C4
    rising-usage FAs UNION C7 handcuffs of any rostered starter league-wide,
    minus E5's injured-star cases (excluded per §1) and minus my own
    starters' handcuffs (self-insurance is C7's flag, not hoarding). Returns
    the deduped candidate ids and the source tags each one matched."""
    fa_ids = [
        pid
        for pid, meta in ctx.players.items()
        if meta.get("espn_team_id") is None
    ]
    fa_by_name = {ctx.players[pid]["name"]: pid for pid in fa_ids}

    sources: Dict[int, List[str]] = {pid: [] for pid in fa_ids}

    # top-N by rate
    ranked = sorted(
        fa_ids, key=lambda pid: ctx.rates.get(pid, 0.0), reverse=True
    )
    for pid in ranked[:HOARD_POOL_TOP_N]:
        sources[pid].append("top_rate")

    # C4 rising-usage FAs (match by name; usage shifts are name-keyed)
    for name in usage_shift_names:
        pid = fa_by_name.get(name)
        if pid is not None and "usage_shift" not in sources[pid]:
            sources[pid].append("usage_shift")

    # C7 handcuffs of any rostered starter league-wide
    rostered_names: Set[str] = set()
    for ids in ctx.rosters.values():
        for pid in ids:
            rostered_names.add(ctx.players[pid]["name"])
    my_roster_names = {
        ctx.players[pid]["name"] for pid in ctx.rosters.get(my_team, [])
    }
    own_starter_handcuff_ids: Set[int] = set()
    for starter_name, handcuff_name in handcuff_map.items():
        if starter_name not in rostered_names:
            continue
        pid = fa_by_name.get(handcuff_name)
        if pid is None:
            continue
        if starter_name in my_roster_names:
            # my own starter's handcuff = self-insurance, C7 owns it (§1).
            # Excluded from the pool entirely (not just the handcuff source)
            # so a high-rate own-handcuff can't sneak in via top_rate.
            own_starter_handcuff_ids.add(pid)
            continue
        if "handcuff" not in sources[pid]:
            sources[pid].append("handcuff")

    # the pool is the union of sourced FAs, minus E5's exclusions and minus
    # my own starters' handcuffs (self-insurance, §1)
    pool = [pid for pid in fa_ids if sources[pid]]
    pool = [
        pid
        for pid in pool
        if pid not in e5_excluded and pid not in own_starter_handcuff_ids
    ]
    return pool, sources


def _entry_copy(
    player: dict,
    hoard_value: float,
    my_gain: float,
    best_rival_gain: float,
    drop_value: float,
    reason: str,
    sources: List[str],
    rival_team_id: Optional[int],
    ctx: ValuationContext,
) -> str:
    """C8-framed one-liner: quotes ROS points (hoard value, gain, drop
    cost) and the source story (role/usage), never last week's score."""
    name = player.get("name") or "player"
    pos = player.get("position") or ""
    per_week = hoard_value / len(ctx.horizon) if ctx.horizon else 0.0
    if reason == "upside":
        gain_phrase = f"worth {my_gain:.1f} ROS points to your lineup"
        if "usage_shift" in sources:
            gain_phrase = (
                f"rising usage — {my_gain:.1f} ROS points to your lineup"
            )
        elif "handcuff" in sources:
            gain_phrase = (
                f"a rostered starter's backup — {my_gain:.1f} ROS points "
                "of upside to your lineup"
            )
        lead = gain_phrase
    else:
        rival = ctx.team_names.get(rival_team_id, "a rival")
        lead = (
            f"{best_rival_gain:.1f} ROS points of denial value vs {rival} "
            f"(they'd start him)"
        )
    return (
        f"{name} ({pos}) — {lead}, "
        f"{hoard_value:.1f} hoard value ({per_week:.1f}/wk) against a "
        f"{drop_value:.1f}-point drop. Claimable until Sunday."
    )


def _compute_hoarding_entries(
    ctx: ValuationContext,
    my_team: int,
    rivals: List[int],
    my_roster_slots: Dict[int, str],
    handcuff_map: Dict[str, str],
    e5_excluded: Set[int],
    usage_shift_names: Set[str],
) -> Tuple[List[dict], Optional[str]]:
    """The pure worth-hoarding scan over the bounded candidate pool, given
    a built context and the extra inputs build_context doesn't carry.
    Returns (entries, note) — entries sorted by margin desc, capped at
    HOARD_REPORT_MAX."""
    drop = _drop_candidate(ctx, my_team, my_roster_slots, handcuff_map)
    if drop is None:
        return [], "no droppable player"
    drop_id, drop_value = drop
    drop_player = ctx.players[drop_id]
    drop_entry = {
        "player_id": drop_id,
        "player_name": drop_player.get("name"),
        "value": round(drop_value, 1),
    }

    pool, sources = _candidate_pool(
        ctx, handcuff_map, usage_shift_names, e5_excluded, my_team
    )

    # my_gain for all candidates; best_rival_gain only for those within
    # reach (spec §2.3). Candidates that clear on my_gain alone are flagged
    # upside with no rival scan.
    gains: Dict[int, float] = {}
    cleared_upside: List[int] = []
    for pid in pool:
        mg = _my_gain(ctx, my_team, drop_id, pid)
        gains[pid] = mg
        if mg - drop_value > HOARD_MARGIN:
            cleared_upside.append(pid)

    # rival scan set: candidates not clearing on my_gain, either within
    # reach (my_gain - drop_value > 0) or in the top-N by rate
    need_rival = [pid for pid in pool if pid not in set(cleared_upside)]
    within_reach = [pid for pid in need_rival if gains[pid] - drop_value > 0]
    rest = [pid for pid in need_rival if pid not in set(within_reach)]
    rest_top = sorted(
        rest, key=lambda pid: ctx.rates.get(pid, 0.0), reverse=True
    )[:HOARD_RIVAL_SCAN_N]
    rival_scan_set = set(within_reach) | set(rest_top)

    rival_gains: Dict[int, float] = {}
    rival_teams: Dict[int, Optional[int]] = {}
    for pid in rival_scan_set:
        gain, team = _best_rival_gain(ctx, rivals, pid)
        rival_gains[pid] = gain
        rival_teams[pid] = team

    entries: List[dict] = []
    for pid in pool:
        mg = gains[pid]
        if pid in rival_gains:
            brg = rival_gains[pid]
        elif pid in cleared_upside:
            brg = 0.0  # already clears on upside — rival scan skipped
        else:
            brg = 0.0  # not scanned and not clearing — can't flag
        hoard_value = max(mg, DENIAL_WEIGHT * brg)
        margin = hoard_value - drop_value
        if margin <= HOARD_MARGIN:
            continue
        reason = "denial" if DENIAL_WEIGHT * brg > mg else "upside"
        player = ctx.players[pid]
        entries.append(
            {
                "player_id": pid,
                "player_name": player.get("name"),
                "position": player.get("position"),
                "nfl_team": player.get("nfl_team"),
                "hoard_value": round(hoard_value, 1),
                "reason": reason,
                "my_gain": round(mg, 1),
                "best_rival_gain": round(brg, 1),
                "rival_team_id": rival_teams.get(pid) if reason == "denial" else None,
                "drop": drop_entry,
                "margin": round(margin, 1),
                "sources": sources.get(pid, []),
                "copy": _entry_copy(
                    player,
                    hoard_value,
                    mg,
                    brg,
                    drop_value,
                    reason,
                    sources.get(pid, []),
                    rival_teams.get(pid),
                    ctx,
                ),
            }
        )

    entries.sort(key=lambda e: e["margin"], reverse=True)
    entries = entries[:HOARD_REPORT_MAX]

    note: Optional[str] = None
    # two-entry conflict: multiple targets share one drop — only one move
    # is executable (spec §4). Don't solve the matching problem; the human
    # picks. (All entries share the same drop by construction here.)
    if len(entries) > 1:
        note = (
            f"{len(entries)} hoard targets share one drop — only one move "
            "is executable; pick the one you trust."
        )
    return entries, note


# --- async: stored report + digest notification -------------------------------


async def _replace_report(engine, report: HoardingReport) -> None:
    """Idempotent per-league-week replacement (like every sync scope):
    delete any existing report for this (league, season, week), then save."""
    await engine.get_collection(HoardingReport).delete_many(
        {
            "espn_league_id": report.espn_league_id,
            "season": report.season,
            "week": report.week,
        }
    )
    await engine.save(report)


async def _ensure_hoarding_notification(
    engine, league, week: int, entries: List[dict]
) -> Optional[object]:
    """ONE digest notification per league-week (spec §3 delivery). Created
    only when the report has >= 1 entry; body names the top entry and the
    count. Individual entries never push separately — E4 owns interrupting."""
    top = entries[0]
    count = len(entries)
    plural = "targets" if count > 1 else "target"
    body = (
        f"Hoarding scan for week {week}: {count} {plural} worth grabbing "
        f"before Sunday. Top: {top['player_name']} "
        f"({top['hoard_value']} hoard value, {top['reason']})."
    )
    return await ensure_notification(
        engine,
        kind="hoarding_report",
        dedupe_key=f"hoard:{league.espn_league_id}:{league.season}:w{week}",
        title=f"Week {week}: {count} hoard {plural}",
        body=body,
        espn_league_id=league.espn_league_id,
        season=league.season,
        week=week,
    )


async def _scan_one_league(engine, league: InSeasonLeague, my_team: int) -> dict:
    """Build the context, gather the extra inputs, run the pure scan, store
    the report, and raise the digest notification for one league."""
    ctx = await build_context(engine, league)
    week = ctx.w0

    # latest FA snapshot for the no-snapshot edge case (spec §4)
    snapshot = await engine.find_one(
        FreeAgentSnapshot,
        (FreeAgentSnapshot.espn_league_id == league.espn_league_id)
        & (FreeAgentSnapshot.season == league.season)
        & (FreeAgentSnapshot.week == week),
        sort=(
            query.desc(FreeAgentSnapshot.synced_at),
            query.desc(FreeAgentSnapshot.id),
        ),
    )
    if snapshot is None:
        report = HoardingReport(
            espn_league_id=league.espn_league_id,
            season=league.season,
            week=week,
            entries=[],
            note="no free agent snapshot for the week",
        )
        await _replace_report(engine, report)
        return {
            "espn_league_id": league.espn_league_id,
            "week": week,
            "entries": 0,
            "note": report.note,
        }

    # my roster's lineup slots (build_context drops lineup_slot; we need it
    # for the IR-stash and drop-candidate exclusions)
    my_roster = await engine.find_one(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == league.espn_league_id)
        & (TeamWeekRoster.season == league.season)
        & (TeamWeekRoster.week == week)
        & (TeamWeekRoster.espn_team_id == my_team),
    )
    my_roster_slots = (
        {e.player_id: e.lineup_slot for e in my_roster.entries}
        if my_roster
        else {}
    )

    pairs = await list_handcuffs(engine)
    handcuff_map = {pair.starter_name: pair.handcuff_name for pair in pairs}

    e5_excluded = await rival_injured_star_handcuff_ids(
        engine, league.espn_league_id, league.season, week
    )

    shifts = await detect_usage_shifts(engine, league.season, week)
    usage_shift_names = {
        s["player_name"] for s in shifts if s["direction"] == "rising"
    }

    rivals = [
        team.espn_team_id
        for team in league.teams
        if team.espn_team_id != my_team
    ]

    entries, note = _compute_hoarding_entries(
        ctx,
        my_team,
        rivals,
        my_roster_slots,
        handcuff_map,
        e5_excluded,
        usage_shift_names,
    )

    report = HoardingReport(
        espn_league_id=league.espn_league_id,
        season=league.season,
        week=week,
        entries=entries,
        note=note,
    )
    await _replace_report(engine, report)

    if entries:
        await _ensure_hoarding_notification(engine, league, week, entries)

    return {
        "espn_league_id": league.espn_league_id,
        "week": week,
        "entries": len(entries),
        "note": note,
    }


async def run_hoarding_scan(
    engine, season: int, now: Optional[datetime.datetime] = None
) -> dict:
    """
    The scheduler entry point: for every synced league the user owns a
    team in (ESPN_MY_TEAMS), run the post-waivers worth-hoarding scan,
    store the report, and raise the digest notification. Guarded by
    HOARDING_ENABLED and HOARD_WEEKDAYS (spec §3 scheduling). Leagues
    missing from ESPN_MY_TEAMS are skipped — hoarding is first-person by
    definition (spec §4). Wired by the orchestrator into the scheduler;
    never called from the GET path.
    """
    if not hoarding_should_run(now):
        return {"scanned": 0, "reason": "disabled_or_wrong_day"}

    leagues = await engine.find(
        InSeasonLeague, InSeasonLeague.season == season
    )
    results = []
    for league in leagues:
        my_team = ESPN_MY_TEAMS.get(league.espn_league_id)
        if my_team is None:
            continue  # ESPN_MY_TEAMS missing the league -> skip (spec §4)
        if not any(team.espn_team_id == my_team for team in league.teams):
            continue  # my team id not in this league's teams
        result = await _scan_one_league(engine, league, my_team)
        results.append(result)
    return {"scanned": len(results), "leagues": results}
