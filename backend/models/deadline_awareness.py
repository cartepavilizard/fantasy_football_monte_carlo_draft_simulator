# -*- coding: utf-8 -*-
"""
TRADE DEADLINE AWARENESS (PHASE E, TASK E8)

Per-league trade-deadline tracking from `InSeasonLeague.trade_deadline`. In
the configurable number of weeks before the deadline, produces buy/sell
window flags per team using a contender-vs-rebuilder lens from
`LeagueTeamInfo` wins/losses. When E1 values are available, the contender
lens quotes the `playoff_value` component (E1 §4.1: a reported component,
never a re-weighting) — that's exactly the number that decides whether a
contender should buy. Creates deduped notifications via `ensure_notification`
(B5's backbone) so the scheduler can re-run freely.

`run_deadline_check(engine, season)` is the orchestrator hook (wired later by
the scheduler, mirroring how C1's LineupPullScheduler calls its async core):
it iterates every synced league for the season, computes windows, attaches
E1 playoff_value where buildable, and writes notifications.

NO E1 HARD DEPENDENCY: a league with no roster/FA data still gets role/window
flags — E1 enrichment is best-effort and degrades gracefully. A league with
no `trade_deadline` produces no flags and no crash (the deadline just isn't
known; nothing to count down to).

Mongo-only, like the rest of the cached-only read path: no `data_sources`
import, direct or transitive.
"""
import datetime
import math
import os
from typing import Dict, List, Optional

from .config import DRAFT_YEAR
from .inseason import InSeasonLeague, LeagueTeamInfo
from .notifications import ensure_notification

# --- tunables (env-overridable; never edit models/config.py) -------------------

# How many weeks before the deadline the buy/sell window is open.
DEADLINE_WINDOW_WEEKS = int(os.getenv("E8_DEADLINE_WINDOW_WEEKS", "3"))

# Win-rate thresholds for the contender/rebuilder lens. A team between the
# two is "neutral" (no flag) — the model only calls when there's a clear
# strategic reason to act before the deadline.
CONTENDER_MIN_WIN_PCT = float(os.getenv("E8_CONTENDER_MIN_WIN_PCT", "0.6"))
REBUILDER_MAX_WIN_PCT = float(os.getenv("E8_REBUILDER_MAX_WIN_PCT", "0.4"))

# Minimum decided games before a record is trusted for the lens — week 1's
# 1-0 is not contender evidence.
MIN_DECIDED_GAMES = int(os.getenv("E8_MIN_DECIDED_GAMES", "3"))

# Notification kind + dedupe key prefix. Dedupe is per league-season-week-team
# -window, so the scheduler re-running inside one scoring period is a no-op.
NOTIFICATION_KIND = "deadline_window"


def weeks_until_deadline(
    trade_deadline: Optional[datetime.datetime],
    now: Optional[datetime.datetime] = None,
) -> Optional[int]:
    """Whole weeks remaining until the deadline (ceil), or None when there
    is no deadline. Negative means the deadline has passed."""
    if trade_deadline is None:
        return None
    now = now or datetime.datetime.now()
    delta_days = (trade_deadline - now).total_seconds() / 86400.0
    return math.ceil(delta_days / 7.0)


def team_record(team: LeagueTeamInfo) -> dict:
    """Win pct over decided games (ties count as half a win, per convention)."""
    decided = team.wins + team.losses + team.ties
    if decided <= 0:
        return {"decided": 0, "win_pct": 0.0}
    wins = team.wins + 0.5 * team.ties
    return {"decided": decided, "win_pct": wins / decided}


def team_role(team: LeagueTeamInfo) -> str:
    """contender | rebuilder | neutral — the strategic lens from wins/losses.
    Below MIN_DECIDED_GAMES everything is neutral: September records aren't
    evidence (same credibility rule C2/C4/E3 apply to social data)."""
    record = team_record(team)
    if record["decided"] < MIN_DECIDED_GAMES:
        return "neutral"
    pct = record["win_pct"]
    if pct >= CONTENDER_MIN_WIN_PCT:
        return "contender"
    if pct <= REBUILDER_MAX_WIN_PCT:
        return "rebuilder"
    return "neutral"


def window_for_role(role: str) -> Optional[str]:
    """Contenders BUY (chase playoff-value pieces); rebuilder SELL (move
    expiring assets for future value). Neutral -> no window."""
    if role == "contender":
        return "buy"
    if role == "rebuilder":
        return "sell"
    return None


def compute_deadline_windows(
    league: InSeasonLeague,
    now: Optional[datetime.datetime] = None,
) -> dict:
    """
    PURE: per-team deadline flags for one league. No Mongo reads, no E1
    values, no notifications. Returns the report skeleton the API serves
    and `run_deadline_check` enriches.

    A league with no `trade_deadline` returns `in_window=False` and an empty
    `teams` list — graceful, no flags, no crash.
    """
    now = now or datetime.datetime.now()
    deadline = league.trade_deadline
    weeks_left = weeks_until_deadline(deadline, now)
    if deadline is None or weeks_left is None:
        return {
            "espn_league_id": league.espn_league_id,
            "season": league.season,
            "week": league.latest_scoring_period,
            "trade_deadline": None,
            "weeks_to_deadline": None,
            "in_window": False,
            "teams": [],
        }

    in_window = 0 <= weeks_left <= DEADLINE_WINDOW_WEEKS
    teams: List[dict] = []
    for team in league.teams:
        role = team_role(team)
        window = window_for_role(role)
        record = team_record(team)
        entry = {
            "espn_team_id": team.espn_team_id,
            "name": team.name,
            "wins": team.wins,
            "losses": team.losses,
            "ties": team.ties,
            "win_pct": round(record["win_pct"], 3),
            "decided_games": record["decided"],
            "role": role,
            "window": window,
            "playoff_value": None,  # filled by _attach_playoff_values when E1 is available
        }
        teams.append(entry)
    return {
        "espn_league_id": league.espn_league_id,
        "season": league.season,
        "week": league.latest_scoring_period,
        "trade_deadline": deadline.isoformat(),
        "weeks_to_deadline": weeks_left,
        "in_window": in_window,
        "teams": teams,
    }


async def _attach_playoff_values(engine, league: InSeasonLeague, report: dict) -> dict:
    """
    Best-effort E1 enrichment: for each team, the sum of `playoff_value`
    (E1 §4.1 — the playoff-window ROS points above replacement, a REPORTED
    component that is never re-weighted into the headline value) across
    their current roster. This is the number that makes a contender's buy
    window actionable.

    Degrades gracefully: if E1's context can't be built (no rosters, no FA
    snapshot, any failure), `playoff_value` stays None on every team and
    the role/window flags are unchanged. E1 is not a hard dependency.
    """
    try:
        # Imported lazily so a future E1 import problem can never break the
        # deadline check, and so this module's import graph stays minimal.
        from .trade_valuation import build_context, player_value

        ctx = await build_context(engine, league)
    except Exception:
        return report

    by_team: Dict[int, float] = {}
    for team in report["teams"]:
        total = 0.0
        for pid in ctx.rosters.get(team["espn_team_id"], []):
            try:
                total += float(player_value(ctx, pid).get("playoff_value", 0.0) or 0.0)
            except Exception:
                continue
        by_team[team["espn_team_id"]] = round(total, 1)

    for team in report["teams"]:
        team["playoff_value"] = by_team.get(team["espn_team_id"])
    return report


def _deadline_notification(team_entry: dict, report: dict) -> dict:
    """Build the notification fields for one team's window flag."""
    window = team_entry["window"]
    role = team_entry["role"]
    weeks = report["weeks_to_deadline"]
    deadline_str = report.get("trade_deadline")
    if window == "buy":
        pv = team_entry.get("playoff_value")
        pv_phrase = (
            f" Your roster carries about {pv:.1f} playoff-window ROS points "
            "above replacement — the buy math is what closes that gap."
            if pv is not None
            else ""
        )
        title = f"{team_entry['name']}: trade-deadline buy window"
        body = (
            f"{team_entry['name']} is {team_entry['wins']}-"
            f"{team_entry['losses']} with ~{weeks} week(s) to the trade "
            f"deadline ({deadline_str}) — a contender's buy window.{pv_phrase}"
        )
    else:  # sell
        title = f"{team_entry['name']}: trade-deadline sell window"
        body = (
            f"{team_entry['name']} is {team_entry['wins']}-"
            f"{team_entry['losses']} with ~{weeks} week(s) to the trade "
            f"deadline ({deadline_str}) — a rebuilder's sell window: move "
            "expiring assets for future value."
        )
    return {
        "kind": NOTIFICATION_KIND,
        "dedupe_key": (
            f"{report['espn_league_id']}:{report['season']}:w{report['week']}:"
            f"team{team_entry['espn_team_id']}:{window}"
        ),
        "title": title,
        "body": body,
        "espn_league_id": report["espn_league_id"],
        "season": report["season"],
        "week": report["week"],
        "event_at": None,
    }


async def run_deadline_check(
    engine,
    season: int = DRAFT_YEAR,
    now: Optional[datetime.datetime] = None,
) -> List[dict]:
    """
    Orchestrator hook (wired later by the scheduler): for every synced
    league this season inside its deadline window, compute per-team buy/sell
    flags, attach E1 `playoff_value` where buildable, and create deduped
    notifications (one per team with a window; idempotent inside a scoring
    period via `ensure_notification`). Returns the list of reports.

    Leagues with no `trade_deadline`, or outside the window, produce no
    flags and no notifications — and never crash.
    """
    now = now or datetime.datetime.now()
    leagues = await engine.find(InSeasonLeague, InSeasonLeague.season == season)
    reports: List[dict] = []
    for league in leagues:
        report = compute_deadline_windows(league, now=now)
        if not report["in_window"]:
            reports.append(report)
            continue
        await _attach_playoff_values(engine, league, report)
        for team in report["teams"]:
            if not team.get("window"):
                continue
            fields = _deadline_notification(team, report)
            await ensure_notification(engine, **fields)
        reports.append(report)
    return reports


async def deadline_report(
    engine,
    espn_league_id: int,
    season: int = DRAFT_YEAR,
    now: Optional[datetime.datetime] = None,
) -> Optional[dict]:
    """The API's read path: one league's deadline report, E1-enriched,
    no notification writes. None when the league isn't synced."""
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        return None
    report = compute_deadline_windows(league, now=now)
    if report["in_window"]:
        await _attach_playoff_values(engine, league, report)
    return report
