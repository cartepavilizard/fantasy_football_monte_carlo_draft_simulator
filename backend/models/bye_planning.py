# -*- coding: utf-8 -*-
"""
F2 BYE-WEEK PLANNING (PHASE F)

Two display-only awareness features over the NFL schedule:

1. Draft-time bye-clustering warning: given the NFL teams of a league's
   likely starters and a season's ProGame schedule, warn when N or more
   of them share a bye week (N is the env-tunable BYE_CLUSTER_THRESHOLD,
   default 3). A flag — never a rule: it changes no ranking, no value,
   no suggestion. It just tells the user "you're concentrated on week X"
   so they can choose to spread.

2. In-season thin-week preview: for one synced roster, which FUTURE week
   is thinnest from byes (the week the user loses the most starters to
   NFL byes) — so lineup maneuvering and waiver adds can target it.

DEGRADATION: both features degrade gracefully to a "no_schedule_data"
status when ProGame rows are absent (early in the dev cycle, before the
first sync, or for a season the schedule hasn't loaded for). No crash,
no fabricated byes — the caller learns there's nothing to reason about.

A team's bye week = a week in the schedule's range where that NFL team
plays no game. The set of "weeks" is derived from the ProGame rows
themselves (no hardcoded 1..18: a league running an 18-week season and
one running a 17-week season both work, and a partial sync doesn't
invent byes for unsynced weeks).

PURITY / NO-MUTATION INVARIANT: every function here is pure. ProGame
rows may be passed as model objects or dicts; they are read only. Tests
assert inputs are unchanged after a bye computation.

This module imports nothing from data_sources (directly or transitively)
— only the standard library — so it sits inside inseason_api's
cached-only read path. flags_api.py wires it to Mongo.
"""
import os
from typing import Any, Dict, Iterable, List, Optional, Set


# N or more likely starters sharing a bye week triggers the cluster
# warning. Env-tunable so a league that wants a tighter/looser bar can
# dial it without a code change (the threshold IS a judgment, unlike
# F1's rho table which is definitional).
BYE_CLUSTER_THRESHOLD = int(os.getenv("BYE_CLUSTER_THRESHOLD", "3"))

# Slots excluded from "starters" when previewing a synced roster's thin
# week — bench and IR depth doesn't leave a hole in a starting lineup.
BENCH_SLOTS = {"BE", "IR"}

NO_SCHEDULE_STATUS = "no_schedule_data"


# --- schedule helpers (read-only) --------------------------------------------


def _game_week_team(game: Any) -> Optional[tuple]:
    """Coerce one ProGame (model object or dict) to (week, home, away),
    uppercased teams. Returns None if the row lacks the fields."""
    if isinstance(game, dict):
        week = game.get("week")
        home = game.get("home_team")
        away = game.get("away_team")
    else:
        week = getattr(game, "week", None)
        home = getattr(game, "home_team", None)
        away = getattr(game, "away_team", None)
    if week is None:
        return None
    try:
        week = int(week)
    except (TypeError, ValueError):
        return None
    home = str(home).strip().upper() if home else None
    away = str(away).strip().upper() if away else None
    if not home or not away:
        return None
    return week, home, away


def bye_weeks_by_team(
    pro_games: Iterable[Any],
) -> Dict[str, Set[int]]:
    """
    Per NFL team, the set of weeks in the schedule's range where that
    team plays NO game (its byes). A team that appears in no game row
    is absent from the result (we can't know its byes from a schedule
    it isn't in). Pure: `pro_games` is read only.
    """
    games = [_game_week_team(g) for g in pro_games]
    games = [g for g in games if g is not None]
    if not games:
        return {}
    all_weeks: Set[int] = set()
    teams_with_games: Set[str] = set()
    played: Dict[str, Set[int]] = {}
    for week, home, away in games:
        all_weeks.add(week)
        for team in (home, away):
            teams_with_games.add(team)
            played.setdefault(team, set()).add(week)
    byes: Dict[str, Set[int]] = {}
    for team in teams_with_games:
        team_byes = all_weeks - played[team]
        if team_byes:
            byes[team] = team_byes
    return byes


def _team_of(player: Any) -> Optional[str]:
    if isinstance(player, dict):
        team = player.get("nfl_team")
    else:
        team = getattr(player, "nfl_team", None)
    return str(team).strip().upper() if team else None


def _name_of(player: Any) -> Optional[str]:
    if isinstance(player, dict):
        name = player.get("name") or player.get("player_name")
    else:
        name = getattr(player, "name", None) or getattr(
            player, "player_name", None
        )
    name = str(name).strip() if name else None
    return name or None


# --- 1. draft-time bye-cluster warning ---------------------------------------


def bye_cluster_warning(
    league_players: Iterable[Any],
    pro_games: Iterable[Any],
    threshold: int = BYE_CLUSTER_THRESHOLD,
) -> dict:
    """
    Warn when `threshold` or more of `league_players`' NFL teams share a
    bye week. `league_players` may be dicts ({nfl_team, name?, ...}) or
    attribute-bearing objects (Player, RosterSlotEntry). Players with no
    nfl_team are skipped (no fabricated byes).

    Returns:
      {
        "status": "ok" | "no_schedule_data",
        "threshold": int,
        "clusters": [{"week": w, "count": n, "players": [...]}],
        "warning": <str or None>,
      }

    Clusters are sorted by week; within a week, players are sorted by
    name for determinism. Pure: inputs are read only.
    """
    byes = bye_weeks_by_team(pro_games)
    if not byes:
        return {
            "status": NO_SCHEDULE_STATUS,
            "threshold": threshold,
            "clusters": [],
            "warning": None,
            "note": (
                "No NFL schedule data available — bye clustering cannot "
                "be assessed. Sync the league to load the pro schedule."
            ),
        }

    # week -> list of (team, player_name) for players whose team is on
    # bye that week
    affected: Dict[int, List[tuple]] = {}
    for player in league_players:
        team = _team_of(player)
        if team is None or team not in byes:
            continue
        name = _name_of(player)
        for week in byes[team]:
            affected.setdefault(week, []).append((team, name))

    clusters: List[dict] = []
    for week in sorted(affected):
        entries = sorted(affected[week], key=lambda t: (t[1] or "", t[0]))
        count = len(entries)
        if count < threshold:
            continue
        clusters.append(
            {
                "week": week,
                "count": count,
                "players": [
                    {"name": name, "nfl_team": team} for team, name in entries
                ],
            }
        )

    if not clusters:
        return {
            "status": "ok",
            "threshold": threshold,
            "clusters": [],
            "warning": None,
            "note": (
                f"No bye week shared by {threshold}+ likely starters."
            ),
        }

    summary = ", ".join(
        f"week {c['week']} ({c['count']})" for c in clusters
    )
    return {
        "status": "ok",
        "threshold": threshold,
        "clusters": clusters,
        "warning": (
            f"{len(clusters)} bye-week cluster(s) at or above the "
            f"{threshold}-starter threshold: {summary}. Spread "
            "concentration — flag, not a rule."
        ),
        "note": (
            "Bye clustering is an awareness flag; it changes no ranking "
            "or value."
        ),
    }


# --- 2. in-season thin-week preview ------------------------------------------


def thin_week_preview(
    roster_entries: Iterable[Any],
    pro_games: Iterable[Any],
    current_week: int,
) -> dict:
    """
    For one synced roster, the FUTURE week thinnest from byes (the week
    the user loses the most starters to NFL byes). `roster_entries` are
    RosterSlotEntry-like (dict or model: nfl_team, player_name/name,
    lineup_slot). Bench/IR slots are skipped — only starters count
    toward "thinness". `current_week` is ESPN's latest scoring period;
    weeks <= current_week are in the past and ignored.

    Returns:
      {
        "status": "ok" | "no_schedule_data",
        "current_week": int,
        "thinnest_week": <int or None>,
        "count": <int or None>,
        "affected": [{"name", "nfl_team"}],
        "weeks": [{"week", "count", "affected"}],
      }

    The thinnest week is the one with the MOST starters on bye (ties
    broken to the earliest future week). Pure: inputs are read only.
    """
    byes = bye_weeks_by_team(pro_games)
    if not byes:
        return {
            "status": NO_SCHEDULE_STATUS,
            "current_week": current_week,
            "thinnest_week": None,
            "count": None,
            "affected": [],
            "weeks": [],
            "note": (
                "No NFL schedule data available — thin-week preview "
                "cannot be assessed. Sync the league to load the pro "
                "schedule."
            ),
        }

    starters: List[tuple] = []  # (team, name)
    for entry in roster_entries:
        slot = _slot_of(entry)
        if slot in BENCH_SLOTS:
            continue
        team = _team_of(entry)
        if team is None or team not in byes:
            continue
        starters.append((team, _name_of(entry)))

    # future bye weeks only — a week in byes is "future" if > current_week
    future_bye_weeks = sorted(
        w for w in {w for weeks in byes.values() for w in weeks}
        if w > current_week
    )

    weeks_out: List[dict] = []
    for week in future_bye_weeks:
        affected = sorted(
            ((team, name) for team, name in starters if week in byes.get(team, set())),
            key=lambda t: (t[1] or "", t[0]),
        )
        if not affected:
            continue
        weeks_out.append(
            {
                "week": week,
                "count": len(affected),
                "affected": [
                    {"name": name, "nfl_team": team} for team, name in affected
                ],
            }
        )

    if not weeks_out:
        return {
            "status": "ok",
            "current_week": current_week,
            "thinnest_week": None,
            "count": 0,
            "affected": [],
            "weeks": [],
            "note": (
                "No future bye weeks affect this roster's starters."
            ),
        }

    # thinnest = most starters on bye; tie -> earliest week
    thinnest = max(weeks_out, key=lambda w: (w["count"], -w["week"]))
    # but ties must go to the EARLIEST week, so pick min week among max-count
    max_count = thinnest["count"]
    thinnest = min(
        (w for w in weeks_out if w["count"] == max_count),
        key=lambda w: w["week"],
    )
    return {
        "status": "ok",
        "current_week": current_week,
        "thinnest_week": thinnest["week"],
        "count": thinnest["count"],
        "affected": thinnest["affected"],
        "weeks": weeks_out,
        "note": (
            f"Week {thinnest['week']} is thinnest — {thinnest['count']} "
            "starter(s) on bye. Awareness flag, not a lineup command."
        ),
    }


def _slot_of(entry: Any) -> Optional[str]:
    if isinstance(entry, dict):
        slot = entry.get("lineup_slot")
    else:
        slot = getattr(entry, "lineup_slot", None)
    return str(slot).strip().upper() if slot else None
