# -*- coding: utf-8 -*-
"""
USAGE-SHIFT DETECTION (PHASE C, TASK C4 — the frontier core)

Detects meaningful week-over-week changes in a player's volume (snap
share, target share) and raises alerts through B5's notification
backbone. This module is the SIGNAL; the recurring ingestion that
fills PlayerWeekUsage is the cheap half (spec at the bottom).

DATA SOURCE — DECIDED ONCE, HERE: nflverse data releases.
Snap counts and target shares are not in ESPN's league API. Options
considered:
- ESPN player pages: no structured snap data; scraping game logs is
  fragile and per-player (hundreds of requests).
- FantasyPros snap-count pages: scrapes an HTML table that reshuffles;
  no target-share history without more scraping.
- nflverse (https://github.com/nflverse/nflverse-data): free public
  CSV releases, stable documented schemas, one HTTPS GET per file per
  week, updated within a day of games. Snap counts AND weekly player
  stats (which already include computed target_share). No auth, no
  rate-limit pressure, fits the existing Transport seam exactly.
nflverse wins on every axis. The ingestion adapter belongs in
data_sources/nflverse.py (cheap half, spec below).

WHAT IS A "MEANINGFUL SHIFT" VS NOISE — the contract:

  shift = |current week's share - baseline| >= threshold
  baseline = mean of the most recent USAGE_BASELINE_MAX_WEEKS (4)
             prior weeks with data, requiring at least
             USAGE_BASELINE_MIN_WEEKS (2)

- Thresholds: snap share 0.12, target share 0.07 (absolute share
  points). Week-to-week snap share for a stable role oscillates ~±8
  share points with game script; 12 clears that. Target share is
  tighter-distributed (a 25% target share is elite), so 7 points is a
  real role change while staying outside single-game-script noise.
- Minimum weeks: a baseline needs >= 2 prior weeks, so the first
  possible alert is week 3. One week is not a baseline — week 1 -> 2
  "shifts" are mostly matchup script, and September credibility
  matters more than one extra week of alerts.
- Relevance floors: max(current, baseline) must reach 0.15 snap /
  0.10 target share. A 4th-stringer moving 2% -> 13% is churn, not a
  rising role.
- Direction matters both ways: "rising" (the waiver signal — a backup
  emerging before the box score says so) and "falling" (the sell/bench
  signal — a shrinking role on your own roster).

ALERT FRAMING (C8's process-over-results rule, enforced at the source):
titles and bodies speak in volume and opportunity — snaps and targets,
current vs baseline — and NEVER in fantasy points. One week of points
is variance; a role change is signal. The copy templates below are the
convention C8 extends to the rest of the module.

Alerts dedupe through ensure_notification (B5) on
usage:{season}:w{week}:{player}:{metric}:{direction} — safe to re-run
every sync pass. Notifications are only created for players present in
a synced league (any roster, or the free-agent pool — i.e. plausibly
actionable); detection itself is league-independent and returns every
shift for the trends UI.

--- SPEC FOR THE CHEAP HALF (recurring ingestion transform) ----------

1. data_sources/nflverse.py: `NflverseUsageAdapter` styled exactly like
   EspnLeagueAdapter (injectable Transport, RateLimiter(1.0), fetch
   methods that return unsaved model instances). Two sources:
   a. Snap counts CSV:
      https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv
      columns used: week, player, position, team, opponent,
      offense_snaps, offense_pct; keep game_type == "REG" rows only.
      -> PlayerWeekUsage(snaps=offense_snaps, snap_share=offense_pct)
   b. Weekly player stats CSV:
      https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{season}.csv
      columns used: player_display_name, position, recent_team, week,
      targets, target_share, carries, receptions (season_type "REG").
      -> targets/target_share/carries; touches = carries + receptions.
   Parse with csv.DictReader over response text; numeric fields via
   float()/int() with blank -> None. Normalize team abbreviations to
   ESPN's (nflverse uses LA for the Rams; see data_sources/nfl_teams.py
   for the canonical map) before saving.
2. `ingest_usage(engine, season, week=None)` in the same module:
   fetch both files once, merge rows by (player name, week) — snap CSV
   is the spine, stats CSV fills target/carry fields — and REPLACE the
   PlayerWeekUsage scope per (season, week) like every other sync
   (delete_many then save_all). When week is None ingest every week in
   the files. Log failures per source; never raise (B1's pattern).
3. Wire into InSeasonScheduler.run_now after the league sync, guarded
   by a new USAGE_INGEST_ENABLED env (default false), then call
   ensure_usage_shift_notifications(engine, season, week) for the most
   recent COMPLETED week (min over leagues of latest_scoring_period-1;
   usage data always trails the live week).
4. Tests in backend/tests style: FakeTransport with literal CSV text,
   merge behavior, replace-not-duplicate, team-abbrev normalization,
   and the scheduler guard.
----------------------------------------------------------------------
"""
from typing import Dict, List, Optional, Set

from .config import (
    USAGE_BASELINE_MAX_WEEKS,
    USAGE_BASELINE_MIN_WEEKS,
    USAGE_SNAP_FLOOR,
    USAGE_SNAP_SHIFT_THRESHOLD,
    USAGE_TARGET_FLOOR,
    USAGE_TARGET_SHIFT_THRESHOLD,
)
from .inseason import FreeAgentSnapshot, PlayerWeekUsage, TeamWeekRoster
from .notifications import ensure_notification

# metric attribute -> (threshold, relevance floor, human phrase)
USAGE_METRICS = {
    "snap_share": (USAGE_SNAP_SHIFT_THRESHOLD, USAGE_SNAP_FLOOR, "snap share"),
    "target_share": (
        USAGE_TARGET_SHIFT_THRESHOLD,
        USAGE_TARGET_FLOOR,
        "target share",
    ),
}


async def detect_usage_shifts(engine, season: int, week: int) -> List[dict]:
    """
    Every meaningful usage shift for one NFL week, per the module
    contract. League-independent and read-only — safe to call from the
    trends UI as well as the alert path.
    """
    window_start = week - USAGE_BASELINE_MAX_WEEKS
    rows = await engine.find(
        PlayerWeekUsage,
        (PlayerWeekUsage.season == season)
        & (PlayerWeekUsage.week >= window_start)
        & (PlayerWeekUsage.week <= week),
    )
    by_player: Dict[str, Dict[int, PlayerWeekUsage]] = {}
    for row in rows:
        by_player.setdefault(row.player_name, {})[row.week] = row

    shifts = []
    for player_name in sorted(by_player):
        weeks = by_player[player_name]
        current = weeks.get(week)
        if current is None:
            continue
        for metric, (threshold, floor, phrase) in USAGE_METRICS.items():
            value = getattr(current, metric)
            if value is None:
                continue
            priors = [
                getattr(weeks[prior_week], metric)
                for prior_week in range(window_start, week)
                if prior_week in weeks
                and getattr(weeks[prior_week], metric) is not None
            ]
            if len(priors) < USAGE_BASELINE_MIN_WEEKS:
                continue
            baseline = sum(priors) / len(priors)
            delta = value - baseline
            if abs(delta) < threshold or max(value, baseline) < floor:
                continue
            shifts.append(
                {
                    "player_name": player_name,
                    "position": current.position,
                    "nfl_team": current.nfl_team,
                    "season": season,
                    "week": week,
                    "metric": metric,
                    "metric_phrase": phrase,
                    "current": round(value, 4),
                    "baseline": round(baseline, 4),
                    "delta": round(delta, 4),
                    "direction": "rising" if delta > 0 else "falling",
                    "baseline_weeks": len(priors),
                }
            )
    return shifts


async def relevant_player_names(engine, season: int) -> Set[str]:
    """
    Players worth alerting about: anyone on a synced roster or in a
    free-agent pool for this season. Empty when nothing is synced —
    then no alerts fire (there is nothing actionable to alert on).
    """
    names: Set[str] = set()
    for roster in await engine.find(
        TeamWeekRoster, TeamWeekRoster.season == season
    ):
        names.update(entry.player_name for entry in roster.entries)
    for snapshot in await engine.find(
        FreeAgentSnapshot, FreeAgentSnapshot.season == season
    ):
        names.update(entry.player_name for entry in snapshot.entries)
    return names


def _shift_copy(shift: dict) -> tuple:
    """Title/body in volume-and-opportunity language — never points"""
    name = shift["player_name"]
    who = " ".join(
        part for part in [shift["nfl_team"], shift["position"]] if part
    )
    who = f"{name} ({who})" if who else name
    current = f"{shift['current']:.0%}"
    baseline = f"{shift['baseline']:.0%}"
    weeks = shift["baseline_weeks"]
    phrase = shift["metric_phrase"]
    if shift["direction"] == "rising":
        title = f"{name}: {phrase} climbing"
        body = (
            f"{who} earned a {current} {phrase} in week {shift['week']}, up "
            f"from a {baseline} average over the prior {weeks} weeks. "
            "Opportunity is moving first — volume like this usually shows "
            "up in production later."
        )
    else:
        title = f"{name}: {phrase} shrinking"
        body = (
            f"{who} was down to a {current} {phrase} in week "
            f"{shift['week']} against a {baseline} average over the prior "
            f"{weeks} weeks. The role is the signal, not one line in a "
            "box score — worth watching before it costs you a start."
        )
    return title, body


async def ensure_usage_shift_notifications(
    engine,
    season: int,
    week: int,
    relevant_names: Optional[Set[str]] = None,
) -> List:
    """
    Detect and raise deduped usage_shift notifications for one week,
    restricted to players present in a synced league. Idempotent —
    every sync pass can call it.
    """
    if relevant_names is None:
        relevant_names = await relevant_player_names(engine, season)
    created = []
    for shift in await detect_usage_shifts(engine, season, week):
        if shift["player_name"] not in relevant_names:
            continue
        title, body = _shift_copy(shift)
        notification = await ensure_notification(
            engine,
            kind="usage_shift",
            dedupe_key=(
                f"usage:{season}:w{week}:{shift['player_name']}:"
                f"{shift['metric']}:{shift['direction']}"
            ),
            title=title,
            body=body,
            season=season,
            week=week,
        )
        if notification is not None:
            created.append(notification)
    return created
