# -*- coding: utf-8 -*-
"""
FULL LINEUP OPTIMIZER (PHASE C, TASK C1; C6's lock rules land here too)

Best legal lineup per league-week from weekly projections plus C2's
matchup tilt, honoring each league's real slot rules from the synced
league settings. Reads Mongo only — it lives behind inseason_api's
cached-only constraint; freshness comes from the sync paths (B3's loop,
POST /inseason/sync, and the Thursday-morning pull in scheduler.py).

PROJECTION SOURCE — THE KEY DESIGN DECISION, MADE DELIBERATELY:

The weekly baseline is ESPN's own weekly projection, as already synced
into RosterSlotEntry.projected_points (statSourceId=1 for the scoring
period). Chosen over deriving weekly numbers from the season-long
rankings blend because:

1. League-scoring correctness. ESPN applies each league's actual
   scoring settings to appliedTotal — the three leagues score
   differently, and a blend-derived number would need a re-scoring
   model per league to compete.
2. Week awareness. ESPN's weekly number already reflects opponent,
   injury designations, and Vegas context. The season blend has no
   weekly decomposition; deriving one (season / 17, adjusted) would
   throw away exactly the freshness a Thursday decision needs.
3. Zero new fetch surface. The numbers arrive with every roster sync,
   so the optimizer inherits Phase B's freshness/staleness story
   instead of needing its own.

Tradeoff accepted: ESPN's weekly projection already prices the matchup
to some degree, which is WHY C2's adjustment is a capped tilt (see
models/matchup_strength.py) rather than a full multiplier.

The seam: every projection enters through weekly_projections() /
the optimize_lineup(projections=...) override — one mapping of
player_id -> weekly points. Swapping the source (e.g. a future blend
of ESPN + external weekly projections) means swapping what fills that
mapping; nothing downstream knows where the numbers came from.

OPTIMIZATION: slots expand to instances (RB x2 -> RB, RB), players are
matched to instances by position eligibility, and an exact DP over
(slot index, used-player bitmask) maximizes total adjusted projection —
roster sizes make this trivial (< 100k states) and exact, so no greedy
edge cases with overlapping flex types (RB/WR vs WR/TE vs OP). A tiny
fill bonus inside the DP prefers starting a zero-projection player over
an empty slot without ever distorting a real comparison.

C6 — LINEUP-LOCKING STRATEGY (the decision rule, defined here):

Players lock individually at their game's kickoff, so WHERE a player
sits when they lock determines which slots stay playable later in the
week. Two rules:

1. Arrangement rule (zero cost, always applied): among assignments with
   the same optimal total, prefer the one that keeps flexible slots
   unlocked longest — early-locking players go to their most
   restrictive eligible slot, late-locking players hold the flex-type
   slots. Implemented as a second DP over the chosen starter set (the
   total is a property of the SET, so every legal re-arrangement of it
   scores the same and this is free EV) maximizing sum of
   slot_flexibility x kickoff_lateness. The Wednesday opener needs no
   special case: the rules key on kickoff times, wherever they fall.
2. Margin rule (advice, never auto-applied): when ANY starter locks
   early (kickoff >= EARLY_LOCK_LEAD_HOURS before the week's final
   lock) and a bench player eligible for that slot kicks off later
   within LOCK_FLEX_MARGIN_POINTS of the starter's adjusted projection,
   surface the swap as an option — starting the later player keeps the
   whole call open until their kickoff. (Rule 1 already ensures the
   early starter's SLOT does minimal damage; this rule is about the
   start/sit decision itself.) Rationale: keeping the decision open is
   an option worth ~P(pivot needed) x E(points recovered) ~= 10-15% x
   6-8 pts ~= 0.6-1.2 pts, hence the 1.0-point default margin. The
   optimizer still recommends the higher projection; the advice
   quantifies what the flexibility would cost, and the user — who
   knows whether questionable tags loom elsewhere — decides.
"""
import datetime
from typing import Dict, List, Optional

from .config import (
    EARLY_LOCK_LEAD_HOURS,
    ESPN_MY_TEAMS,
    LOCK_FLEX_MARGIN_POINTS,
)
from .inseason import InSeasonLeague, ProGame, TeamWeekRoster
from .notifications import ensure_notification
from .matchup_strength import (
    defense_position_strength,
    matchup_adjusted,
    strength_for,
)

# Starting slots and the positions each accepts. BE/IR are the bench.
STARTING_SLOT_POSITIONS = {
    "QB": {"QB"},
    "TQB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "RB/WR": {"RB", "WR"},
    "WR/TE": {"WR", "TE"},
    "FLEX": {"RB", "WR", "TE"},
    "OP": {"QB", "RB", "WR", "TE"},
    "K": {"K"},
    "DST": {"DST"},
}

# Canonical display/solve order: dedicated slots before flex-type
SLOT_ORDER = ["QB", "TQB", "RB", "WR", "TE", "RB/WR", "WR/TE", "FLEX", "OP", "K", "DST"]

# C6: how many positions a slot accepts = how valuable keeping it
# unlocked is (a locked FLEX forecloses more pivots than a locked QB)
SLOT_FLEXIBILITY = {
    slot: len(positions) for slot, positions in STARTING_SLOT_POSITIONS.items()
}

# Injury statuses that make starting someone a warning, not just a choice
HARD_INJURY_STATUSES = {"out", "injury_reserve", "suspension", "doubtful"}

# DP fill bonus: prefer any eligible body over an empty slot, but keep
# it far below any real projection difference
_FILL_BONUS = 1e-6


def weekly_projections(roster: TeamWeekRoster) -> Dict[int, Optional[float]]:
    """THE PROJECTION SEAM (see module docstring): player_id -> weekly
    baseline. Default source is ESPN's weekly projection as synced."""
    return {entry.player_id: entry.projected_points for entry in roster.entries}


def slot_instances(lineup_slot_counts: Dict[str, int]) -> List[str]:
    """The league's starting slots expanded to instances, canonical order"""
    instances = []
    for slot in SLOT_ORDER:
        instances.extend([slot] * lineup_slot_counts.get(slot, 0))
    return instances


def best_assignment(slots, candidates, weights):
    """
    Exact max-total assignment of candidates to slot instances.
    candidates: list of (player_id, position); weights: player_id ->
    float. Returns (assignment, total) where assignment maps slot index
    -> player_id (missing index = slot left empty, only when nothing
    eligible remains).
    """
    memo = {}

    def solve(i, used):
        if i == len(slots):
            return 0.0, {}
        key = (i, used)
        if key in memo:
            return memo[key]
        best_total, best_map = solve(i + 1, used)  # slot left empty
        eligible_positions = STARTING_SLOT_POSITIONS[slots[i]]
        for j, (player_id, position) in enumerate(candidates):
            if used & (1 << j) or position not in eligible_positions:
                continue
            sub_total, sub_map = solve(i + 1, used | (1 << j))
            total = (weights.get(player_id) or 0.0) + _FILL_BONUS + sub_total
            if total > best_total + 1e-12:
                best_total = total
                best_map = dict(sub_map)
                best_map[i] = player_id
        memo[key] = (best_total, best_map)
        return memo[key]

    _, assignment = solve(0, 0)
    total = sum(weights.get(player_id) or 0.0 for player_id in assignment.values())
    return assignment, round(total, 2)


def arrange_for_lock_flexibility(slots, assignment, candidates, kickoffs):
    """
    C6 rule 1: re-arrange the CHOSEN starter set (the total is a
    property of the set, so this is free) to keep flexible slots
    unlocked longest — maximize sum of slot_flexibility * lateness rank.
    """
    chosen = set(assignment.values())
    starters = [(pid, pos) for pid, pos in candidates if pid in chosen]
    if not starters:
        return assignment
    # lateness rank in [0, 1]: earliest kickoff 0, latest 1; unknown
    # kickoff (bye) counts earliest — it never usefully locks a slot
    times = sorted(
        {kickoffs[pid] for pid, _ in starters if kickoffs.get(pid) is not None}
    )
    span = max(len(times) - 1, 1)
    lateness = {
        pid: (times.index(kickoffs[pid]) / span if kickoffs.get(pid) else 0.0)
        for pid, _ in starters
    }

    memo = {}
    full_mask = (1 << len(starters)) - 1

    def solve(i, used):
        if i == len(slots):
            # every chosen starter must land somewhere or the
            # arrangement isn't the same lineup
            return (0.0, {}) if used == full_mask else (None, None)
        key = (i, used)
        if key in memo:
            return memo[key]
        best_score, best_map = solve(i + 1, used)  # slot stays empty
        eligible_positions = STARTING_SLOT_POSITIONS[slots[i]]
        for j, (player_id, position) in enumerate(starters):
            if used & (1 << j) or position not in eligible_positions:
                continue
            sub_score, sub_map = solve(i + 1, used | (1 << j))
            if sub_score is None:
                continue
            score = SLOT_FLEXIBILITY[slots[i]] * lateness[player_id] + sub_score
            if best_score is None or score > best_score + 1e-12:
                best_score = score
                best_map = dict(sub_map)
                best_map[i] = player_id
        memo[key] = (best_score, best_map)
        return memo[key]

    score, arrangement = solve(0, 0)
    return arrangement if arrangement is not None else assignment


def _fmt_kickoff(moment: Optional[datetime.datetime]) -> Optional[str]:
    if moment is None:
        return None
    time_part = moment.strftime("%I:%M %p").lstrip("0")
    return f"{moment.strftime('%a')} {time_part}"


def lock_advice(slots, assignment, candidates, weights, kickoffs, final_lock):
    """
    C6 rule 2 (advice only, never auto-applied): flag any starter who
    locks early when a later-kicking bench alternative eligible for
    their slot sits within the margin. One suggestion per slot.
    """
    if final_lock is None:
        return []
    advice = []
    chosen = set(assignment.values())
    early_cutoff = final_lock - datetime.timedelta(hours=EARLY_LOCK_LEAD_HOURS)
    for slot_index, player_id in sorted(assignment.items()):
        slot = slots[slot_index]
        kickoff = kickoffs.get(player_id)
        if kickoff is None or kickoff > early_cutoff:
            continue
        occupant_points = weights.get(player_id) or 0.0
        for candidate_id, position in candidates:
            if candidate_id in chosen or position not in STARTING_SLOT_POSITIONS[slot]:
                continue
            alt_kickoff = kickoffs.get(candidate_id)
            alt_points = weights.get(candidate_id) or 0.0
            if alt_kickoff is None or alt_kickoff <= kickoff:
                continue
            cost = round(occupant_points - alt_points, 2)
            if cost > LOCK_FLEX_MARGIN_POINTS:
                continue
            advice.append(
                {
                    "slot": slot,
                    "start": player_id,
                    "alternative": candidate_id,
                    "cost_points": max(cost, 0.0),
                    "note": (
                        f"{slot} locks {_fmt_kickoff(kickoff)}. A bench "
                        f"alternative kicks off {_fmt_kickoff(alt_kickoff)} at "
                        f"a cost of {max(cost, 0.0):.1f} projected points — "
                        "starting the later player keeps this slot open in "
                        "case you need to pivot before Sunday."
                    ),
                }
            )
            break  # one suggestion per slot is enough
    return advice


async def optimize_lineup(
    engine,
    league: InSeasonLeague,
    espn_team_id: int,
    week: int,
    projections: Optional[Dict[int, float]] = None,
    strength: Optional[dict] = None,
) -> Optional[dict]:
    """
    The full lineup call for one team-week: optimal legal lineup from
    adjusted projections, the moves that get there from the current
    lineup, per-player matchup context, and C6 lock guidance.
    Returns None when no synced roster exists for that team-week.
    """
    roster = await engine.find_one(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == league.espn_league_id)
        & (TeamWeekRoster.season == league.season)
        & (TeamWeekRoster.week == week)
        & (TeamWeekRoster.espn_team_id == espn_team_id),
    )
    if roster is None:
        return None
    if strength is None:
        strength = await defense_position_strength(engine, league.season)

    games = await engine.find(
        ProGame, (ProGame.season == league.season) & (ProGame.week == week)
    )
    team_kickoffs = {}
    opponents = {}
    for game in games:
        for team, other in (
            (game.home_team, game.away_team),
            (game.away_team, game.home_team),
        ):
            if team not in team_kickoffs or game.kickoff < team_kickoffs[team]:
                team_kickoffs[team] = game.kickoff
                opponents[team] = other
    final_lock = max(team_kickoffs.values()) if team_kickoffs else None

    base = weekly_projections(roster)  # the seam
    if projections:
        base.update(projections)

    annotated = {}
    weights = {}
    kickoffs = {}
    candidates = []
    ir_players = []
    warnings = []
    for entry in roster.entries:
        opponent = opponents.get(entry.nfl_team)
        matchup = strength_for(strength, entry.position, opponent)
        base_points = base.get(entry.player_id)
        adjusted = matchup_adjusted(base_points, matchup["multiplier"])
        kickoff = team_kickoffs.get(entry.nfl_team)
        annotated[entry.player_id] = {
            "player_id": entry.player_id,
            "player_name": entry.player_name,
            "position": entry.position,
            "nfl_team": entry.nfl_team,
            "injury_status": entry.injury_status,
            "current_slot": entry.lineup_slot,
            "base_projection": base_points,
            "adjusted_projection": adjusted,
            "opponent": opponent,
            "on_bye": opponent is None,
            "kickoff": kickoff.isoformat() if kickoff else None,
            "matchup": matchup,
        }
        if entry.lineup_slot == "IR":
            ir_players.append(entry.player_id)
            continue  # can't be started without a roster move
        weights[entry.player_id] = adjusted
        kickoffs[entry.player_id] = kickoff
        candidates.append((entry.player_id, entry.position))

    slots = slot_instances(league.lineup_slot_counts)
    assignment, optimal_total = best_assignment(slots, candidates, weights)
    assignment = arrange_for_lock_flexibility(slots, assignment, candidates, kickoffs)
    advice = lock_advice(slots, assignment, candidates, weights, kickoffs, final_lock)

    optimal_slot_by_player = {
        player_id: slots[slot_index] for slot_index, player_id in assignment.items()
    }
    current_total = 0.0
    for entry in roster.entries:
        if entry.lineup_slot in STARTING_SLOT_POSITIONS:
            current_total += annotated[entry.player_id]["adjusted_projection"] or 0.0
            if entry.injury_status in HARD_INJURY_STATUSES:
                warnings.append(
                    f"{entry.player_name} is in your {entry.lineup_slot} slot "
                    f"but listed {entry.injury_status}"
                )
            if annotated[entry.player_id]["on_bye"] and team_kickoffs:
                warnings.append(
                    f"{entry.player_name} is in your {entry.lineup_slot} slot "
                    "but has no game this week (bye)"
                )
    current_total = round(current_total, 2)

    moves = []
    for entry in roster.entries:
        if entry.player_id in ir_players:
            continue
        current = (
            entry.lineup_slot
            if entry.lineup_slot in STARTING_SLOT_POSITIONS
            else "BE"
        )
        target = optimal_slot_by_player.get(entry.player_id, "BE")
        if current != target:
            moves.append(
                {
                    "player_id": entry.player_id,
                    "player_name": entry.player_name,
                    "from_slot": current,
                    "to_slot": target,
                }
            )

    optimal = [
        {
            "slot": slot,
            "player": (
                annotated[assignment[index]] if index in assignment else None
            ),
        }
        for index, slot in enumerate(slots)
    ]
    started = set(assignment.values())
    for slot_entry in optimal:
        player = slot_entry["player"]
        if player and player["injury_status"] in HARD_INJURY_STATUSES:
            warnings.append(
                f"Optimal lineup starts {player['player_name']} "
                f"({player['injury_status']}) — projections may lag news"
            )
    bench = [
        annotated[player_id]
        for player_id, _ in candidates
        if player_id not in started
    ]
    if not any(
        entry["player"] and entry["player"]["matchup"]["weeks_sampled"]
        for entry in optimal
    ):
        warnings.append(
            "Matchup adjustments are neutral — no completed weeks synced "
            "yet, so projections are unadjusted ESPN weekly numbers"
        )

    return {
        "week": week,
        "espn_team_id": espn_team_id,
        "optimal": optimal,
        "bench": bench,
        "ir": [annotated[player_id] for player_id in ir_players],
        "current_total": current_total,
        "optimal_total": optimal_total,
        "delta_points": round(optimal_total - current_total, 2),
        "moves": moves,
        "lock_advice": advice,
        "warnings": warnings,
    }


async def ensure_lineup_review(
    engine,
    espn_league_id: int,
    season: int,
    week: int,
    my_teams: Optional[Dict[int, int]] = None,
):
    """
    The Thursday-morning pull's notification (one per league-week,
    deduped): when the user's team in this league is known
    (ESPN_MY_TEAMS), the body quotes that team's optimizer delta;
    otherwise it just says fresh data is in. Volume-and-moves language
    only — never one-week results (C8's framing).
    """
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        return None
    if my_teams is None:
        my_teams = ESPN_MY_TEAMS
    team_id = my_teams.get(espn_league_id)
    result = (
        await optimize_lineup(engine, league, team_id, week)
        if team_id is not None
        else None
    )
    if result is None:
        body = (
            f"Fresh week-{week} data pulled for {league.name} — open the "
            "lineup optimizer to review this week's calls before rosters lock."
        )
    elif result["moves"]:
        body = (
            f"Fresh data is in for {league.name}. The optimal lineup "
            f"projects {result['optimal_total']:.1f} points — "
            f"+{result['delta_points']:.1f} over your current lineup with "
            f"{len(result['moves'])} move(s) to make."
        )
    else:
        body = (
            f"Fresh data is in for {league.name}. Your current lineup is "
            f"already optimal ({result['optimal_total']:.1f} projected points)."
        )
    return await ensure_notification(
        engine,
        kind="lineup_review",
        dedupe_key=f"{espn_league_id}:{season}:w{week}:lineup_review",
        title=f"Week {week} lineup review: {league.name}",
        body=body,
        espn_league_id=espn_league_id,
        season=season,
        week=week,
    )
