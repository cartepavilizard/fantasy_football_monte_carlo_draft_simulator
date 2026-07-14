# -*- coding: utf-8 -*-
"""
TRADE VALUATION MODEL (PHASE E, TASK E1)

The keystone of Phase E — E2 (counters), E4 (opportunity scanner), E5
(blocking), E6 (hoarding), E7 (messaging), and E8 (deadline lens) all
consume the two value units and the pure functions defined here. Reads
Mongo only, so it joins B4's cached-only club: no data_sources import,
and its two routes are covered by both enforcement tests in
test_inseason_api.py.

THE TWO VALUE UNITS (defined once, used everywhere; never merged):

- player_value  — context-free market value: expected league-scoring
  fantasy points a player produces ABOVE REPLACEMENT over the remaining
  fantasy-relevant weeks ("ROS points"), floored at zero (you can always
  drop a player, so negative trade value does not exist). Answers "is
  this trade lopsided on raw value?".
- fit_delta     — roster-context value: the change in a specific roster's
  expected STARTING-LINEUP ROS points caused by a move. Signed (a trade
  can hurt), never floored. Answers "does it help THIS roster?".

Everything is in league-scoring points because the underlying numbers
are ESPN's per-league weekly projections (C1's decided source), so values
are per-league — never compare a player_value from league 111 with one
from league 222.

PURITY CONTRACT (load-bearing for E2): everything below build_context is
synchronous and pure. build_context does every Mongo read and every rate
computation once; E2 builds one context and evaluates hundreds of
candidate trades against it with no awaits in the path.

The full design contract — every constant's rationale, the availability
curve, the worked examples that double as this module's test fixtures —
is docs/specs/E1-trade-valuation.md. Reconciliations made against that
spec's normative fixtures are noted in E1's row of EXECUTION_PLAN_FEATURES.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import (
    BENCH_FACTOR,
    DOUBTFUL_PLAY_PROB,
    FAIR_GAP_FRACTION,
    FAIR_GAP_POINTS,
    IR_RETURN_DISCOUNT,
    IR_RETURN_WEEKS,
    PLAYOFF_SOS_WEEKS,
    QUESTIONABLE_PLAY_PROB,
    RATE_MIN_POINTS,
    REPLACEMENT_RANK,
    TRADE_HORIZON_FINAL_WEEK,
    TRADE_RATE_WEEKS,
)
from .inseason import (
    FreeAgentSnapshot,
    InjuryDesignation,
    TeamWeekRoster,
)
from .lineup import best_assignment, slot_instances
from .matchup_strength import (
    defense_position_strength,
    matchup_adjusted,
    opponent_map,
    strength_for,
)

# InjuryDesignation.designation (D2's vocabulary) -> the lowercased ESPN
# status the availability curve keys on. Unmapped values pass through
# unchanged (they are already ESPN statuses).
_DESIGNATION_TO_STATUS = {
    "ir": "injury_reserve",
    "pup": "injury_reserve",  # PUP is an IR-like stash: unknown timeline
    "active": "active",
}

# Statuses whose curve opens with zeroed weeks — a roster spot spent now
# for production later. player_value attaches a stash_note for these.
_STASH_STATUSES = {"injury_reserve", "suspension"}


@dataclass
class ValuationContext:
    """Everything an evaluation needs, built once per request by
    build_context. Pure functions read this and nothing else."""

    league: object                       # InSeasonLeague (or minimal stand-in)
    season: int
    w0: int                              # the live week (latest_scoring_period)
    horizon: List[int]                   # [w0 .. W_final]
    playoff_weeks: List[int]             # horizon INTERSECT PLAYOFF_SOS_WEEKS
    opponents: Dict[tuple, str]          # (nfl_team, week) -> opponent, opponent_map()
    strength: dict                       # defense_position_strength() table
    rates: Dict[int, float]              # player_id -> neutral weekly rate
    players: Dict[int, dict]             # player_id -> {name, position, nfl_team,
    #                                      injury_status, espn_team_id|None}
    replacement: Dict[str, float]        # position -> rr (the zero line)
    rosters: Dict[int, List[int]]        # espn_team_id -> w0 roster player_ids
    team_names: Dict[int, str]           # espn_team_id -> team name
    warnings: List[str] = field(default_factory=list)


# --- horizon / availability (pure) -------------------------------------------


def horizon_for(league, week: Optional[int] = None):
    """[w0 .. W_final]: the live week through the fantasy championship.
    Week 17 production is worthless in these leagues, so value stops at
    min(TRADE_HORIZON_FINAL_WEEK, final_scoring_period or 17)."""
    w0 = week or league.latest_scoring_period
    final = league.final_scoring_period or 17
    w_final = min(TRADE_HORIZON_FINAL_WEEK, final)
    return w0, list(range(w0, w_final + 1))


def availability_curve(
    status: Optional[str],
    horizon: List[int],
    overrides: Optional[Dict[int, float]] = None,
) -> Dict[int, float]:
    """Per-week play probability keyed by week over the horizon (weeks
    counted from w0 = horizon[0]). This table IS the IR-stash value — an
    IR player's worth is whatever survives the curve. Overrides are
    explicit request input (a manually trusted return timeline); they
    replace the curve for the named weeks and are never persisted."""
    status = (status or "active").lower()
    curve: Dict[int, float] = {}
    for index, week in enumerate(horizon):
        if status == "questionable":
            prob = QUESTIONABLE_PLAY_PROB if index == 0 else 1.0
        elif status == "doubtful":
            prob = DOUBTFUL_PLAY_PROB if index == 0 else 1.0
        elif status == "out":
            # next week usually questionable, then full
            prob = (0.0, QUESTIONABLE_PLAY_PROB)[index] if index < 2 else 1.0
        elif status in _STASH_STATUSES:
            # duration unknown for suspension -> same conservative curve
            prob = 0.0 if index < IR_RETURN_WEEKS else IR_RETURN_DISCOUNT
        else:
            prob = 1.0  # active / None / unknown
        curve[week] = prob
    if overrides:
        for week, prob in overrides.items():
            if week in curve:
                curve[week] = prob
    return curve


def expected_points(
    ctx: ValuationContext,
    player_id: int,
    week: int,
    overrides: Optional[Dict[int, float]] = None,
) -> float:
    """epts(p, w): matchup-adjusted neutral rate times play probability.
    Zero on a bye (opponent is None for a known team). A player with no
    nfl_team gets neutral multipliers and NO bye zeroing (we cannot know
    their schedule)."""
    player = ctx.players[player_id]
    team = player.get("nfl_team")
    if team is not None:
        opponent = ctx.opponents.get((team, week))
        if opponent is None:
            return 0.0  # bye (or no scheduled game) for a known team
    else:
        opponent = None  # unknown team: neutral, never a bye
    multiplier = strength_for(ctx.strength, player.get("position"), opponent)[
        "multiplier"
    ]
    adjusted = matchup_adjusted(ctx.rates.get(player_id, 0.0), multiplier) or 0.0
    prob = availability_curve(player.get("injury_status"), ctx.horizon, overrides)[
        week
    ]
    return adjusted * prob


# --- the two headline computations (pure) ------------------------------------


def _player_overrides(overrides, player_id):
    return overrides.get(player_id) if overrides else None


def player_value(
    ctx: ValuationContext,
    player_id: int,
    overrides: Optional[Dict[int, Dict[int, float]]] = None,
) -> dict:
    """Market value: gross ROS points minus replacement charged over the
    whole horizon, floored at zero. playoff_value is a REPORTED component
    computed from its own window — never a re-weighting of the headline
    unit, and never zeroed just because the headline floored (the playoff
    component is the reason a zero-market IR stash is still tradeable)."""
    player = ctx.players[player_id]
    position = player.get("position")
    rr = ctx.replacement.get(position, 0.0)
    my_overrides = _player_overrides(overrides, player_id)
    horizon_len = len(ctx.horizon)

    gross = sum(
        expected_points(ctx, player_id, week, my_overrides) for week in ctx.horizon
    )
    playoff_gross = sum(
        expected_points(ctx, player_id, week, my_overrides)
        for week in ctx.playoff_weeks
    )
    value = max(gross - rr * horizon_len, 0.0)
    playoff_value = max(playoff_gross - rr * len(ctx.playoff_weeks), 0.0)
    per_week = value / horizon_len if horizon_len else 0.0

    warnings = []
    if rr == 0.0 and position:
        warnings.append(
            f"no free agents at {position} — values are raw points, inflated"
        )
    if not player.get("nfl_team"):
        warnings.append(
            f"no NFL team for {player.get('name')} — matchups neutral, no bye zeroing"
        )

    stash_note = _stash_note(
        ctx, player_id, player, gross, playoff_gross, my_overrides
    )

    return {
        "player_id": player_id,
        "name": player.get("name"),
        "position": position,
        "nfl_team": player.get("nfl_team"),
        "injury_status": player.get("injury_status"),
        "rate": round(ctx.rates.get(player_id, 0.0), 2),
        "gross": round(gross, 1),
        "value": round(value, 1),
        "playoff_value": round(playoff_value, 1),
        "per_week": round(per_week, 1),
        "stash_note": stash_note,
        "warnings": warnings,
    }


def _stash_note(ctx, player_id, player, gross, playoff_gross, my_overrides):
    """Plain-terms note for an IR/suspension stash: when they project
    back, the raw points on offer, and how many land in the playoff
    window — the numbers that decide whether the roster spot is worth it."""
    status = (player.get("injury_status") or "").lower()
    if status not in _STASH_STATUSES:
        return None
    curve = availability_curve(status, ctx.horizon, my_overrides)
    back_week = next((w for w in ctx.horizon if curve.get(w, 0.0) > 0.0), None)
    lead = "on IR" if status == "injury_reserve" else "suspended"
    back = f"projected back ~week {back_week}" if back_week else "no return in view"
    return (
        f"{lead}, {back}; {round(gross, 1)} raw pts incl. "
        f"{round(playoff_gross, 1)} in the playoff window — stash value only "
        "if you can afford the spot"
    )


def team_ros_points(
    ctx: ValuationContext,
    player_ids: List[int],
    overrides: Optional[Dict[int, Dict[int, float]]] = None,
) -> float:
    """Roster context: for each horizon week, C1's exact assignment DP
    (best_assignment, reused as-is) picks the starting lineup by epts,
    plus a discounted bench-depth term. Candidates include EVERY player
    on the roster (IR occupants too — their curve zeroes the stash weeks,
    and in return weeks they compete for slots). This is what fit_delta
    differences; it is never floored."""
    slots = slot_instances(ctx.league.lineup_slot_counts)
    candidates = [
        (pid, ctx.players[pid].get("position")) for pid in player_ids
    ]
    total = 0.0
    for week in ctx.horizon:
        weights = {
            pid: expected_points(ctx, pid, week, _player_overrides(overrides, pid))
            for pid in player_ids
        }
        assignment, starting = best_assignment(slots, candidates, weights)
        started = set(assignment.values())
        bench = sum(
            max(weights[pid] - ctx.replacement.get(ctx.players[pid].get("position"), 0.0), 0.0)
            for pid in player_ids
            if pid not in started
        )
        total += starting + BENCH_FACTOR * bench
    return total


# --- trade evaluation (pure) -------------------------------------------------


def validate_trade(ctx, team_a, team_b, sends_a, sends_b) -> List[str]:
    """The §6 422 cases, returned as human strings (the endpoint raises
    the first). A player must be on the claimed team's w0 roster, and no
    player may appear twice (same side or across sides)."""
    errors = []
    roster_a = set(ctx.rosters.get(team_a, []))
    roster_b = set(ctx.rosters.get(team_b, []))
    for pid in sends_a:
        if pid not in roster_a:
            errors.append(
                f"player {pid} is not on team {team_a}'s current roster"
            )
    for pid in sends_b:
        if pid not in roster_b:
            errors.append(
                f"player {pid} is not on team {team_b}'s current roster"
            )
    seen = set()
    for pid in list(sends_a) + list(sends_b):
        if pid in seen:
            errors.append(f"player {pid} appears on both sides of the trade")
        seen.add(pid)
    return errors


def evaluate_trade(
    ctx: ValuationContext,
    team_a: int,
    team_b: int,
    sends_a: List[int],
    sends_b: List[int],
    overrides: Optional[Dict[int, Dict[int, float]]] = None,
) -> dict:
    """Grade a proposal on both lenses. Market: sum of player_value each
    side sends, verdict from the fair band. Fit: team_ros_points after
    minus before for each roster (never floored). The two numbers are
    deliberately not merged; the summary presents both."""
    horizon_len = len(ctx.horizon)

    sends_a_values = [player_value(ctx, pid, overrides) for pid in sends_a]
    sends_b_values = [player_value(ctx, pid, overrides) for pid in sends_b]
    value_sent_a = sum(v["value"] for v in sends_a_values)
    value_sent_b = sum(v["value"] for v in sends_b_values)
    market_gap = value_sent_a - value_sent_b
    fair_bound = max(
        FAIR_GAP_POINTS, FAIR_GAP_FRACTION * max(value_sent_a, value_sent_b)
    )
    if abs(market_gap) <= fair_bound:
        verdict = "fair"
    elif market_gap > fair_bound:
        verdict = "favors_b"  # A gives more -> B wins
    else:
        verdict = "favors_a"

    before_a = list(ctx.rosters.get(team_a, []))
    before_b = list(ctx.rosters.get(team_b, []))
    after_a = [p for p in before_a if p not in sends_a] + list(sends_b)
    after_b = [p for p in before_b if p not in sends_b] + list(sends_a)
    fit_delta_a = team_ros_points(ctx, after_a, overrides) - team_ros_points(
        ctx, before_a, overrides
    )
    fit_delta_b = team_ros_points(ctx, after_b, overrides) - team_ros_points(
        ctx, before_b, overrides
    )
    fit_per_week_a = fit_delta_a / horizon_len if horizon_len else 0.0
    fit_per_week_b = fit_delta_b / horizon_len if horizon_len else 0.0

    warnings = list(ctx.warnings)
    for value in sends_a_values + sends_b_values:
        warnings.extend(value["warnings"])
    # de-dupe while preserving order
    warnings = list(dict.fromkeys(warnings))

    summary = _build_summary(
        market_gap,
        fair_bound,
        verdict,
        horizon_len,
        fit_delta_a,
        fit_delta_b,
        sends_a_values,
        sends_b_values,
        warnings,
    )

    return {
        "week": ctx.w0,
        "weeks_remaining": horizon_len,
        "teams": {
            "a": {"espn_team_id": team_a, "name": ctx.team_names.get(team_a)},
            "b": {"espn_team_id": team_b, "name": ctx.team_names.get(team_b)},
        },
        "sends_a": sends_a_values,
        "sends_b": sends_b_values,
        "value_sent_a": round(value_sent_a, 1),
        "value_sent_b": round(value_sent_b, 1),
        "market_gap": round(market_gap, 1),
        "fair_bound": round(fair_bound, 1),
        "verdict": verdict,
        "fit_delta_a": round(fit_delta_a, 1),
        "fit_delta_b": round(fit_delta_b, 1),
        "fit_per_week_a": round(fit_per_week_a, 1),
        "fit_per_week_b": round(fit_per_week_b, 1),
        "summary": summary,
        "warnings": warnings,
    }


def _positions(values) -> str:
    seen = [v["position"] for v in values if v.get("position")]
    ordered = list(dict.fromkeys(seen))
    return "/".join(ordered) if ordered else "player"


def _build_summary(
    market_gap,
    fair_bound,
    verdict,
    horizon_len,
    fit_delta_a,
    fit_delta_b,
    sends_a_values,
    sends_b_values,
    warnings,
) -> str:
    """Plain-terms copy from both lenses (the brainstorm requirement),
    quoting ROS points and per-week points, C8 volume/projection framing.
    Team A is 'you'."""
    per_week_gap = abs(market_gap) / horizon_len if horizon_len else 0.0
    fit_pw_a = fit_delta_a / horizon_len if horizon_len else 0.0
    fit_pw_b = fit_delta_b / horizon_len if horizon_len else 0.0

    if market_gap > 0:
        market = (
            f"You send {abs(market_gap):.1f} more ROS points of market value "
            f"(about {per_week_gap:.1f}/week)"
        )
    elif market_gap < 0:
        market = (
            f"You receive {abs(market_gap):.1f} more ROS points of market value "
            f"(about {per_week_gap:.1f}/week)"
        )
    else:
        market = "The two sides carry equal ROS market value"

    if verdict == "fair":
        fairness = "inside the fair range on value"
    elif verdict == "favors_b":
        fairness = "outside the fair range — it favors them on value"
    else:
        fairness = "outside the fair range — it favors you on value"

    incoming = _positions(sends_b_values)
    fit = (
        f"the deal brings you {incoming}: your starting lineup projects "
        f"{fit_pw_a:+.1f} points/week while theirs gains {fit_pw_b:+.1f}"
    )

    parts = [f"{market}, {fairness}.", f"On roster fit, {fit}."]

    stash = next(
        (v["stash_note"] for v in sends_a_values + sends_b_values if v["stash_note"]),
        None,
    )
    if stash:
        parts.append(f"Stash context: {stash}.")
    if warnings:
        parts.append(f"Heads up — {warnings[0]}.")
    return " ".join(parts)


# --- context construction (the only async surface) ---------------------------


def _qualifies(points) -> bool:
    return points is not None and points >= RATE_MIN_POINTS


def _rate_from(proj_by_week: Dict[int, float], w0: int, season_projection):
    """The neutral weekly rate: mean of the most recent up to
    TRADE_RATE_WEEKS qualifying projections from weeks BEFORE w0, then the
    fallback chain. NOTE: the spec formula reads 'weeks <= w0' but its
    normative fixtures (A.1's identical trailing set for healthy vs
    questionable-at-w0 X; A.5 labelling the week-1/current projection
    'fallback 1') require the trailing window to be strictly < w0, with
    w0's own projection as fallback 1. See E1's EXECUTION_PLAN row."""
    trailing = sorted(
        (w for w in proj_by_week if w < w0 and _qualifies(proj_by_week[w])),
        reverse=True,
    )[:TRADE_RATE_WEEKS]
    if trailing:
        return sum(proj_by_week[w] for w in trailing) / len(trailing), None
    # fallback 1: current-week projection if present
    current = proj_by_week.get(w0)
    if current is not None:
        return current, None
    # fallback 2: season projection spread across a 17-week season
    if season_projection is not None:
        return season_projection / 17.0, None
    # fallback 3: nothing to go on
    return 0.0, "no_projection"


async def build_context(engine, league, week: Optional[int] = None) -> ValuationContext:
    """Load every Mongo input once and compute every rate — the only
    async work. Everything downstream is pure so E2 can reuse one context
    across hundreds of candidate evaluations."""
    season = league.season
    w0, horizon = horizon_for(league, week)
    playoff_weeks = [w for w in horizon if w in PLAYOFF_SOS_WEEKS]

    opponents = await opponent_map(engine, season)
    strength = await defense_position_strength(engine, season)

    # Rosters for the rate window (trailing TRADE_RATE_WEEKS) + current week
    low_week = max(1, w0 - TRADE_RATE_WEEKS)
    rosters = await engine.find(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == league.espn_league_id)
        & (TeamWeekRoster.season == season)
        & (TeamWeekRoster.week >= low_week)
        & (TeamWeekRoster.week <= w0),
    )
    fa_snapshots = await engine.find(
        FreeAgentSnapshot,
        (FreeAgentSnapshot.espn_league_id == league.espn_league_id)
        & (FreeAgentSnapshot.season == season)
        & (FreeAgentSnapshot.week >= low_week)
        & (FreeAgentSnapshot.week <= w0),
    )
    designations = await engine.find(
        InjuryDesignation,
        (InjuryDesignation.season == season) & (InjuryDesignation.week == w0),
    )
    designation_by_name = {d.player_name: d.designation for d in designations}

    # Per-player projection history (both roster and FA rows are the same
    # ESPN number; players move between the two across weeks).
    proj_by_player_week: Dict[int, Dict[int, float]] = {}
    season_proj: Dict[int, float] = {}
    for roster in rosters:
        for entry in roster.entries:
            proj_by_player_week.setdefault(entry.player_id, {})[roster.week] = (
                entry.projected_points
            )
    for snapshot in fa_snapshots:
        for entry in snapshot.entries:
            proj_by_player_week.setdefault(entry.player_id, {})[snapshot.week] = (
                entry.projected_points
            )
            if entry.season_projection is not None:
                season_proj[entry.player_id] = entry.season_projection

    # w0 identity rows: current rosters (with team ownership) and the
    # latest free-agent pool. These define ctx.players.
    w0_rosters = [r for r in rosters if r.week == w0]
    latest_fa = None
    for snapshot in fa_snapshots:
        if latest_fa is None or snapshot.week > latest_fa.week:
            latest_fa = snapshot

    players: Dict[int, dict] = {}
    team_rosters: Dict[int, List[int]] = {}
    warnings: List[str] = []

    def effective_status(name, raw_status):
        designation = designation_by_name.get(name)
        if designation is not None:
            mapped = _DESIGNATION_TO_STATUS.get(
                designation.lower(), designation.lower()
            )
            return mapped
        return raw_status

    for roster in w0_rosters:
        ids = []
        for entry in roster.entries:
            players[entry.player_id] = {
                "name": entry.player_name,
                "position": entry.position,
                "nfl_team": entry.nfl_team,
                "injury_status": effective_status(
                    entry.player_name, entry.injury_status
                ),
                "espn_team_id": roster.espn_team_id,
            }
            ids.append(entry.player_id)
        team_rosters[roster.espn_team_id] = ids

    if latest_fa is not None:
        for entry in latest_fa.entries:
            # a rostered player also surfacing in the pool stays owned
            if entry.player_id in players:
                continue
            players[entry.player_id] = {
                "name": entry.player_name,
                "position": entry.position,
                "nfl_team": entry.nfl_team,
                "injury_status": effective_status(
                    entry.player_name, entry.injury_status
                ),
                "espn_team_id": None,
            }

    # Every rate computed once.
    rates: Dict[int, float] = {}
    for player_id, meta in players.items():
        rate, flag = _rate_from(
            proj_by_player_week.get(player_id, {}), w0, season_proj.get(player_id)
        )
        rates[player_id] = rate
        if flag == "no_projection":
            warnings.append(f"no projection data for {meta['name']}")

    # Replacement level: the REPLACEMENT_RANK-th best free agent at each
    # position from the latest snapshot, ranked by rate.
    replacement: Dict[str, float] = {}
    fa_by_position: Dict[str, List[float]] = {}
    if latest_fa is not None:
        for entry in latest_fa.entries:
            if entry.position:
                fa_by_position.setdefault(entry.position, []).append(
                    rates.get(entry.player_id, 0.0)
                )
    for position, position_rates in fa_by_position.items():
        ranked = sorted(position_rates, reverse=True)
        if len(ranked) >= REPLACEMENT_RANK:
            replacement[position] = ranked[REPLACEMENT_RANK - 1]
        else:
            replacement[position] = ranked[-1]  # fewer than rank: last available

    team_names = {team.espn_team_id: team.name for team in league.teams}

    return ValuationContext(
        league=league,
        season=season,
        w0=w0,
        horizon=horizon,
        playoff_weeks=playoff_weeks,
        opponents=opponents,
        strength=strength,
        rates=rates,
        players=players,
        replacement=replacement,
        rosters=team_rosters,
        team_names=team_names,
        warnings=warnings,
    )
