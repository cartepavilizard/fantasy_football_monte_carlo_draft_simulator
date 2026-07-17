# -*- coding: utf-8 -*-
"""
PROACTIVE TRADE OPPORTUNITY SCANNER (PHASE E, TASK E4)

Cross-references league-wide injury signals against all rosters to flag
trade windows: a rival's starter goes down while you hold real surplus at
that position. The design center (docs/specs/E4-opportunity-scanner.md §1)
is the cost of a false positive — this feature INTERRUPTS the user via
push, so every threshold is set to miss marginal opportunities on
purpose. The release valve for the marginal cases is the report endpoint
(opportunity_api.py): everything the scanner sees, at any severity, is
readable on demand; only the hard triggers page the phone.

THE TRIGGER — five conditions, AND-ed (spec §2):

  1. A real injury event. p's effective status (D2 InjuryDesignation for
     the live week if newer, else ESPN roster injury_status) is out / IR /
     doubtful — AND this status is NEW (differs from the prior scan).
     `questionable` NEVER triggers.
  2. The player mattered. p started for R in the most recent completed
     week, OR E1 rate(p) >= STARTER_RATE_FLOOR.
  3. The rival is actually hurt. R's best same-position alternative is at
     least RIVAL_GAP_POINTS below rate(p).
  4. I have real surplus. Some same-position player s on my roster is
     both spare (one-week removal cost < SURPLUS_COST_CEILING, E2's proxy)
     and attractive (player_value >= SURPLUS_VALUE_FLOOR).
  5. The market lens agrees. A 1-for-1 probe (M sends cheapest surplus s,
     R sends its most movable piece) grades fit_delta_M > 0 in E1.

Conditions 1-4 are cheap; 5 runs only for survivors. Hard triggers (all
five AND status out/IR) push through ensure_notification, rate-limited to
TRADE_WINDOW_PUSHES_PER_WEEK per league-week. Everything else degrades to
a `watch` row in the report.

This module is Mongo-only and joins B4's cached-only club: it imports
nothing from data_sources. It reuses E1's build_context / evaluate_trade /
player_value verbatim and E1's lineup primitives for the one-week surplus
proxy (same as E2's _week_starting) — it does not reimplement E1.
"""
import datetime
import os
from typing import Dict, List, Optional

from odmantic import Model
from odmantic import Field as ODField

from .config import ESPN_MY_TEAMS, SURPLUS_COST_CEILING
from .inseason import InSeasonLeague, TeamWeekRoster
from .lineup import STARTING_SLOT_POSITIONS, best_assignment, slot_instances
from .notifications import Notification, ensure_notification
from .trade_valuation import (
    ValuationContext,
    build_context,
    evaluate_trade,
    expected_points,
    player_value,
)

# Tunables (spec §5 config). Env-overridable, read here in this module
# following models/config.py's style — models/config.py itself is never
# edited (it is outside this task's ownership).
STARTER_RATE_FLOOR = float(os.getenv("STARTER_RATE_FLOOR", 8.0))
RIVAL_GAP_POINTS = float(os.getenv("RIVAL_GAP_POINTS", 3.0))
SURPLUS_VALUE_FLOOR = float(os.getenv("SURPLUS_VALUE_FLOOR", 10.0))
TRADE_WINDOW_PUSHES_PER_WEEK = int(os.getenv("TRADE_WINDOW_PUSHES_PER_WEEK", 2))
TRADE_SCAN_ENABLED = os.getenv("TRADE_SCAN_ENABLED", "false").lower() == "true"

# Statuses that are a "real injury event" for condition 1. `questionable`
# is deliberately absent — it never triggers (spec §2.1, §8).
_REAL_INJURY_STATUSES = {"out", "injury_reserve", "doubtful"}
# The subset that opens a multi-week horizon and is therefore push-eligible
# (spec §3). `doubtful` passes all five but only ever becomes a watch row.
_PUSH_STATUSES = {"out", "injury_reserve"}


class InjuryScanState(Model):
    """Scanner-internal state: the effective status last seen for one
    rostered player in one league. Replaced per scan. Nothing else reads
    it — it exists only so condition 1's "new" check can distinguish a
    fresh injury from a carry-over (spec §4)."""

    model_config = {"collection": "injury_scan_state"}

    espn_league_id: int
    season: int
    player_id: int
    status: Optional[str] = None  # effective status at last scan
    scanned_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- one-week surplus proxy (mirrors E2's _week_starting) -------------------
# A READER built on E1's pure functions: a single week's starting-lineup
# total via C1's exact assignment DP. One week (w0), never the full
# horizon, so the surplus/untouchable filter stays arithmetic. Reuses E1's
# expected_points / best_assignment / slot_instances — does not reimplement
# E1's value model. (Same pattern as models/counterproposals.py.)


def _player_overrides(overrides, player_id):
    return overrides.get(player_id) if overrides else None


def _week_starting(
    ctx: ValuationContext,
    player_ids: List[int],
    week: int,
    overrides=None,
) -> float:
    slots = slot_instances(ctx.league.lineup_slot_counts)
    candidates = [(pid, ctx.players[pid].get("position")) for pid in player_ids]
    weights = {
        pid: expected_points(ctx, pid, week, _player_overrides(overrides, pid))
        for pid in player_ids
    }
    _, starting = best_assignment(slots, candidates, weights)
    return starting


def _removal_cost(ctx, roster, player_id, week, exclude=()) -> float:
    """One-week starting points lost when `player_id` (and any `exclude`
    ids) are removed from `roster`."""
    excluded = {player_id, *exclude}
    full = _week_starting(ctx, roster, week)
    without = _week_starting(
        ctx, [p for p in roster if p not in excluded], week
    )
    return full - without


# --- condition helpers (pure, over one shared ctx) --------------------------


def _best_same_position_alternative(ctx, rival_roster, injured_id, position) -> float:
    """R's highest rate() among its OTHER players at `position` — the
    handcuff / same-caliber backup. 0.0 when R carries nobody else there
    (no backup = a wide-open window, by design)."""
    rates = [
        ctx.rates.get(pid, 0.0)
        for pid in rival_roster
        if pid != injured_id and ctx.players.get(pid, {}).get("position") == position
    ]
    return max(rates) if rates else 0.0


def _surplus_pieces(ctx, my_team_id, position) -> List[dict]:
    """Condition 4: my same-position players who are both SPARE (one-week
    removal cost < SURPLUS_COST_CEILING) and ATTRACTIVE (player_value >=
    SURPLUS_VALUE_FLOOR). A tradable piece must be both — spare bench
    fodder isn't an offer, an attractive starter isn't spare."""
    roster = ctx.rosters.get(my_team_id, [])
    pieces = []
    for pid in roster:
        if ctx.players.get(pid, {}).get("position") != position:
            continue
        value = player_value(ctx, pid)["value"]
        if value < SURPLUS_VALUE_FLOOR:
            continue
        cost = _removal_cost(ctx, roster, pid, ctx.w0)
        if cost >= SURPLUS_COST_CEILING:
            continue
        pieces.append(
            {
                "player_id": pid,
                "name": ctx.players[pid].get("name"),
                "value": round(value, 1),
                "weekly_cost_to_me": round(cost, 1),
            }
        )
    return pieces


def _most_movable(ctx, rival_team_id, injured_id) -> Optional[dict]:
    """Condition 5's R-side: the highest-player_value piece on R's roster
    whose one-week removal cost to R's POST-INJURY lineup (without the
    injured player) is < SURPLUS_COST_CEILING. 'At a position of M's need'
    is left to the fit_delta gate (see Assumptions in notes.md): the probe
    only counts if E1 says the deal helps M, which is exactly the need
    signal. Returns None when nothing is movable."""
    roster = ctx.rosters.get(rival_team_id, [])
    best = None
    for pid in roster:
        if pid == injured_id:
            continue
        cost = _removal_cost(ctx, roster, pid, ctx.w0, exclude={injured_id})
        if cost >= SURPLUS_COST_CEILING:
            continue
        value = player_value(ctx, pid)["value"]
        if best is None or value > best["value"]:
            best = {
                "player_id": pid,
                "name": ctx.players[pid].get("name"),
                "value": round(value, 1),
                "weekly_cost_to_rival": round(cost, 1),
            }
    return best


def _run_probe(ctx, my_team_id, rival_team_id, surplus, movable):
    """Condition 5: a hypothetical 1-for-1 — M sends its CHEAPEST surplus
    piece, R sends its most movable piece — graded by E1. The probe is a
    feasibility check (fit_delta_M > 0), not a recommendation."""
    if not surplus or not movable:
        return None
    s = min(surplus, key=lambda p: p["value"])
    try:
        result = evaluate_trade(
            ctx,
            team_a=my_team_id,
            team_b=rival_team_id,
            sends_a=[s["player_id"]],
            sends_b=[movable["player_id"]],
        )
    except Exception:
        return None
    return result


# --- the core evaluation for one (rival, injured player) --------------------
# Shared by both scan_league (push path) and trade_opportunity_report
# (read-only). `is_new_event` is condition 1's newness flag, supplied by
# the caller: the scan sets it from InjuryScanState; the report treats
# every real injury as a row (it shows ongoing windows, not just new ones).


def _evaluate_opportunity(
    ctx, my_team_id, rival_team_id, injured_id, started_last_week,
    is_new_event, detected_at,
) -> Optional[dict]:
    player = ctx.players.get(injured_id, {})
    status = (player.get("injury_status") or "").lower()
    position = player.get("position")
    rate_p = ctx.rates.get(injured_id, 0.0)

    # Condition 1: a real injury event (caller decides newness).
    if status not in _REAL_INJURY_STATUSES:
        return None
    if not is_new_event:
        return None

    # Condition 2: the player mattered.
    mattered = started_last_week or rate_p >= STARTER_RATE_FLOOR
    if not mattered:
        return None

    rival_roster = ctx.rosters.get(rival_team_id, [])
    rival_name = ctx.team_names.get(rival_team_id) or f"team {rival_team_id}"

    # Condition 3: the rival is actually hurt by it.
    alt_rate = _best_same_position_alternative(ctx, rival_roster, injured_id, position)
    gap_per_week = rate_p - alt_rate
    if gap_per_week < RIVAL_GAP_POINTS:
        return None

    injured = {
        "player_id": injured_id,
        "name": player.get("name"),
        "position": position,
        "status": status,
        "rate": round(rate_p, 1),
    }
    base = {
        "rival_team_id": rival_team_id,
        "rival_team_name": rival_name,
        "injured": injured,
        "rival_gap_per_week": round(gap_per_week, 1),
        "detected_at": detected_at,
    }

    # Conditions 4 & 5 require MY team. Leagues missing from ESPN_MY_TEAMS
    # scan "as nobody" — everything caps at watch, pushes never fire.
    if my_team_id is None:
        return {**base, "severity": "watch", "my_surplus": [],
                "probe": None,
                "note": "no my-team mapping for this league; surplus/probe skipped"}

    surplus = _surplus_pieces(ctx, my_team_id, position)
    if not surplus:
        return {**base, "severity": "watch", "my_surplus": [],
                "probe": None,
                "note": (f"window exists but you have no spare {position} "
                         f"above the {SURPLUS_VALUE_FLOOR:.1f}-point offer floor")}

    movable = _most_movable(ctx, rival_team_id, injured_id)
    probe = _run_probe(ctx, my_team_id, rival_team_id, surplus, movable)
    cond5 = bool(probe and probe.get("fit_delta_a", 0) > 0)

    base["my_surplus"] = surplus
    base["probe"] = probe

    if not cond5:
        note = ("window exists but a 1-for-1 probe does not grade "
                "fit-positive for your roster")
        return {**base, "severity": "watch", "note": note}

    # All five conditions pass. doubtful is a soft row even now (spec §3).
    if status not in _PUSH_STATUSES:
        return {**base, "severity": "watch",
                "note": "doubtful tag — watch only, no push"}

    return {**base, "severity": "window", "note": "all five conditions met"}


# --- state loading / seeding ------------------------------------------------


async def _load_state(engine, espn_league_id, season) -> Dict[int, str]:
    rows = await engine.find(
        InjuryScanState,
        (InjuryScanState.espn_league_id == espn_league_id)
        & (InjuryScanState.season == season),
    )
    return {row.player_id: row.status for row in rows}


async def _persist_state(engine, espn_league_id, season, statuses) -> None:
    """Seed/refresh one row per rostered player. A player first seen
    mid-season seeds silently (no trigger); a player no longer rostered
    simply gets no row — stale rows for dropped players are harmless
    because condition 1 only iterates current rosters."""
    now = datetime.datetime.now()
    existing = {
        row.player_id: row
        for row in await engine.find(
            InjuryScanState,
            (InjuryScanState.espn_league_id == espn_league_id)
            & (InjuryScanState.season == season),
        )
    }
    for player_id, status in statuses.items():
        row = existing.get(player_id)
        if row is None:
            await engine.save(
                InjuryScanState(
                    espn_league_id=espn_league_id,
                    season=season,
                    player_id=player_id,
                    status=status,
                    scanned_at=now,
                )
            )
        elif row.status != status:
            row.status = status
            row.scanned_at = now
            await engine.save(row)


async def _started_last_week(engine, league) -> set:
    """Set of (espn_team_id, player_id) that were in a starting (non
    BE/IR) slot in the most recent completed week (w0 - 1). Week 1 has no
    completed week — returns empty, and condition 2 falls back to rate."""
    w0 = league.latest_scoring_period
    completed = w0 - 1
    if completed < 1:
        return set()
    rosters = await engine.find(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == league.espn_league_id)
        & (TeamWeekRoster.season == league.season)
        & (TeamWeekRoster.week == completed),
    )
    started = set()
    for roster in rosters:
        for entry in roster.entries:
            if entry.lineup_slot in STARTING_SLOT_POSITIONS:
                started.add((roster.espn_team_id, entry.player_id))
    return started


# --- push budget + notification -------------------------------------------


async def _pushes_this_week(engine, espn_league_id, season, week) -> int:
    return await engine.count(
        Notification,
        (Notification.kind == "trade_window")
        & (Notification.espn_league_id == espn_league_id)
        & (Notification.season == season)
        & (Notification.week == week),
    )


def _notification_body(ctx, my_team_id, opp) -> str:
    """C8 framing: the rival's weekly gap, my surplus piece by name and
    per-week value above replacement, and the playoff-window angle when
    the surplus piece's playoff_value is a selling point. Never
    'they're desperate' language — this text may be quoted to a human."""
    surplus = opp.get("my_surplus") or []
    injured = opp.get("injured", {})
    pos = injured.get("position") or "position"
    gap = opp.get("rival_gap_per_week", 0.0)
    parts = [
        f"Their {pos} spot drops ~{gap:.1f} points/week with "
        f"{injured.get('name')} down"
    ]
    if surplus:
        s = surplus[0]
        pv = player_value(ctx, s["player_id"])
        per_week = pv.get("per_week", 0.0)
        parts.append(
            f"and you can spare {s['name']} ({per_week:.1f} pts/week above "
            f"replacement, costs your lineup {s['weekly_cost_to_me']:.1f} this week)"
        )
        playoff = pv.get("playoff_value", 0.0)
        if playoff and playoff >= SURPLUS_VALUE_FLOOR:
            parts.append(
                f"{s['name']} also carries {playoff:.1f} ROS pts in the playoff "
                "window — a real selling point for a contender"
            )
    parts.append("open the trade panel for counters")
    return ", ".join(parts) + "."


# --- the two entry points --------------------------------------------------


async def _scan_core(engine, espn_league_id, season, *, mutate: bool) -> dict:
    """Shared evaluation. `mutate=True` runs the push path (scan_league):
    reads + writes InjuryScanState and creates notifications. `mutate=False`
    is the read-only report (trade_opportunity_report): no writes, no
    notifications, shows every ongoing injury window regardless of
    newness."""
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        return {"week": None, "my_team_id": None, "opportunities": [],
                "error": f"no synced league {espn_league_id}/{season}"}

    ctx = await build_context(engine, league)
    my_team_id = ESPN_MY_TEAMS.get(espn_league_id)
    started = await _started_last_week(engine, league)
    prior_state = await _load_state(engine, espn_league_id, season) if mutate else {}
    # detected_at: the scan moment for the push path; the league's last
    # successful sync for the read-only report (stable across calls so the
    # GET is a pure function of synced data, not of when you hit refresh).
    detected_at = (
        datetime.datetime.now().isoformat() if mutate
        else (league.synced_at.isoformat() if league.synced_at else None)
    )

    all_team_ids = [t.espn_team_id for t in league.teams]
    rival_ids = [tid for tid in all_team_ids if tid != my_team_id]
    opportunities: List[dict] = []
    new_statuses: Dict[int, str] = {}

    # Seed state for EVERY rostered player (mine included — spec §4: one
    # row per rostered player per league); evaluate only rivals (M is not a
    # rival of itself — spec §6).
    for team_id in all_team_ids:
        roster = ctx.rosters.get(team_id, [])
        for pid in roster:
            raw = ctx.players.get(pid, {}).get("injury_status")
            status = raw.lower() if raw else None
            new_statuses[pid] = status
            if team_id == my_team_id:
                continue
            if mutate:
                # Condition 1 newness: a player first ever seen (no prior
                # row) seeds silently; a player with a prior row whose
                # recorded status differs is new. A prior row with status
                # None means the player was previously ACTIVE — that is not
                # "first seen", it is a real baseline the change is measured
                # against (active -> out is the canonical trigger).
                had_prior = pid in prior_state
                is_new_event = had_prior and prior_state[pid] != status
            else:
                # The report shows every current real injury as a row.
                is_new_event = status in _REAL_INJURY_STATUSES
            opp = _evaluate_opportunity(
                ctx, my_team_id, team_id, pid,
                started_last_week=(team_id, pid) in started,
                is_new_event=is_new_event,
                detected_at=detected_at,
            )
            if opp is None:
                continue
            opportunities.append(opp)

    # Push path: create notifications for hard triggers, respecting the
    # per-league-week budget. Budget-suppressed windows degrade to watch.
    if mutate and my_team_id is not None:
        budget = TRADE_WINDOW_PUSHES_PER_WEEK
        used = await _pushes_this_week(engine, espn_league_id, season, ctx.w0)
        for opp in opportunities:
            if opp["severity"] != "window":
                continue
            if used >= budget:
                opp["severity"] = "watch"
                opp["note"] = "suppressed: weekly push budget reached"
                continue
            injured = opp["injured"]
            rival_id = opp["rival_team_id"]
            created = await ensure_notification(
                engine,
                kind="trade_window",
                dedupe_key=(
                    f"tradewin:{espn_league_id}:{season}:{rival_id}:"
                    f"{injured['player_id']}"
                ),
                title=(
                    f"Trade window: {opp['rival_team_name']} just lost "
                    f"{injured['name']} ({injured['status']})"
                ),
                body=_notification_body(ctx, my_team_id, opp),
                espn_league_id=espn_league_id,
                season=season,
                week=ctx.w0,
            )
            if created is not None:
                used += 1

    if mutate:
        await _persist_state(engine, espn_league_id, season, new_statuses)

    return {
        "week": ctx.w0,
        "my_team_id": my_team_id,
        "opportunities": opportunities,
    }


async def scan_league(engine, espn_league_id: int, season: int) -> dict:
    """Run the full scan for one league: evaluate every rival's rostered
    player against the five conditions, update InjuryScanState, and create
    hard-trigger notifications (budget-limited). Returns the report (spec
    §5 shape). Never raises — a failure degrades to a report with an
    `error` field rather than surfacing to the scheduler pass."""
    try:
        return await _scan_core(engine, espn_league_id, season, mutate=True)
    except Exception as exc:  # noqa: BLE001 — B1's never-raise rule
        return {"week": None, "my_team_id": None, "opportunities": [],
                "error": f"scan failed: {exc}"}


async def trade_opportunity_report(
    engine, espn_league_id: int, season: int
) -> dict:
    """Read-only re-run of the conditions WITHOUT state mutation or
    notifications — what the GET serves. Pure Mongo reads so refreshing
    the page never consumes push budget or mutates scan state (spec §5,
    §8). Shows every ongoing injury window (newness is a push concern,
    not a report concern)."""
    return await _scan_core(engine, espn_league_id, season, mutate=False)


async def run_opportunity_scan(engine, season: int) -> dict:
    """Scheduler entry point: scan every synced league for one season.
    Self-guarded by TRADE_SCAN_ENABLED (default false), matching every
    other scheduled producer — the orchestrator wires the schedule, this
    function owns the enabled gate so a misconfigured scheduler can never
    page the phone on a dev box."""
    if not TRADE_SCAN_ENABLED:
        return {"enabled": False, "season": season, "leagues": {}}
    leagues = await engine.find(InSeasonLeague, InSeasonLeague.season == season)
    results = {}
    for league in leagues:
        results[league.espn_league_id] = await scan_league(
            engine, league.espn_league_id, season
        )
    return {"enabled": True, "season": season, "leagues": results}
