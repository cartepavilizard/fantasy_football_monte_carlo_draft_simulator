# -*- coding: utf-8 -*-
"""
NFLVERSE USAGE INGESTION (PHASE C, TASK C4 — the cheap half)

Fills PlayerWeekUsage from nflverse's public CSV releases: snap counts
(snap_share) and weekly player stats (target_share, carries, touches).
See models/usage_shifts.py for why nflverse was chosen over ESPN player
pages or FantasyPros scraping — this module is purely the recurring
ingestion transform; the signal itself (detect_usage_shifts) is
league-independent and lives in models/usage_shifts.py.

Styled exactly like EspnLeagueAdapter: injectable Transport, a
RateLimiter, and fetch methods that return unsaved PlayerWeekUsage
instances. Unlike ESPN, nflverse needs no auth and no per-view failure
isolation beyond "one CSV failed to fetch/parse" — logged as a
LeagueSyncLog row (espn_league_id=None, this is league-independent
data) exactly like sync_pro_schedule, never raised.

MERGE: the snap-counts file is the spine (it has the fullest weekly
player list); the player-stats file only fills target/carry fields onto
matching (player name, week) rows. A player missing from the snap file
entirely is skipped — nflverse's snap-count release covers every
offensive snap-taker, so this only drops non-offensive rows (K/DST),
which is fine: C4's metrics don't apply to them anyway.
"""
import csv
import io
from typing import List, Optional

from models.inseason import LeagueSyncLog, PlayerWeekUsage

from .base import SourceFetchError
from .nfl_teams import normalize_team_abbrev
from .ratelimit import RateLimiter
from .transport import HttpxTransport, Transport

SNAP_COUNTS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "snap_counts/snap_counts_{season}.csv"
)
PLAYER_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "player_stats/player_stats_{season}.csv"
)


def _to_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: Optional[str]) -> Optional[int]:
    parsed = _to_float(value)
    return int(parsed) if parsed is not None else None


class NflverseUsageAdapter:
    """One instance serves both nflverse files; injectable Transport for tests"""

    min_request_interval_seconds = 1.0

    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or HttpxTransport()
        self._rate_limiter = RateLimiter(self.min_request_interval_seconds)

    async def _get_rows(self, url: str) -> List[dict]:
        await self._rate_limiter.wait()
        response = await self.transport.get(url)
        if not response.ok:
            raise SourceFetchError(
                f"nflverse: GET {url} returned {response.status_code}"
            )
        return list(csv.DictReader(io.StringIO(response.text)))

    async def fetch_snap_counts(self, season: int) -> List[PlayerWeekUsage]:
        rows = []
        for row in await self._get_rows(SNAP_COUNTS_URL.format(season=season)):
            if (row.get("game_type") or "") != "REG":
                continue
            week = _to_int(row.get("week"))
            player = (row.get("player") or "").strip()
            if week is None or not player:
                continue
            rows.append(
                PlayerWeekUsage(
                    season=season,
                    week=week,
                    player_name=player,
                    position=(row.get("position") or "").strip() or None,
                    nfl_team=normalize_team_abbrev(row.get("team")),
                    opponent=normalize_team_abbrev(row.get("opponent")),
                    snaps=_to_int(row.get("offense_snaps")),
                    snap_share=_to_float(row.get("offense_pct")),
                )
            )
        return rows

    async def fetch_player_stats(self, season: int) -> List[PlayerWeekUsage]:
        rows = []
        for row in await self._get_rows(PLAYER_STATS_URL.format(season=season)):
            if (row.get("season_type") or "") != "REG":
                continue
            week = _to_int(row.get("week"))
            player = (row.get("player_display_name") or "").strip()
            if week is None or not player:
                continue
            carries = _to_int(row.get("carries"))
            receptions = _to_int(row.get("receptions"))
            touches = None
            if carries is not None or receptions is not None:
                touches = (carries or 0) + (receptions or 0)
            rows.append(
                PlayerWeekUsage(
                    season=season,
                    week=week,
                    player_name=player,
                    position=(row.get("position") or "").strip() or None,
                    nfl_team=normalize_team_abbrev(row.get("recent_team")),
                    targets=_to_int(row.get("targets")),
                    target_share=_to_float(row.get("target_share")),
                    carries=carries,
                    touches=touches,
                )
            )
        return rows


async def _log_failure(engine, season: int, section: str, exc: Exception):
    log = LeagueSyncLog(
        espn_league_id=None,
        season=season,
        section=section,
        success=False,
        error=f"{type(exc).__name__}: {exc}",
        error_kind="http" if isinstance(exc, SourceFetchError) else "parse",
    )
    await engine.save(log)
    return log


async def ingest_usage(
    engine,
    season: int,
    week: Optional[int] = None,
    adapter: Optional[NflverseUsageAdapter] = None,
) -> dict:
    """
    One ingestion pass: fetch both nflverse files, merge by (player name,
    week), and replace the PlayerWeekUsage scope for every week touched
    (or just `week` when given) — delete_many then save_all, per B1's
    sync pattern. Each source's failure is logged and skipped rather
    than raised; if the snap-counts spine is unavailable, nothing is
    written (there's nothing to merge stats onto).
    """
    adapter = adapter or NflverseUsageAdapter()
    summary = {"season": season, "week": week, "sources": {}, "weeks_replaced": []}

    try:
        snap_rows = await adapter.fetch_snap_counts(season)
        summary["sources"]["snap_counts"] = {"success": True, "rows": len(snap_rows)}
    except Exception as exc:
        await _log_failure(engine, season, "usage_snap_counts", exc)
        summary["sources"]["snap_counts"] = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        snap_rows = []

    try:
        stat_rows = await adapter.fetch_player_stats(season)
        summary["sources"]["player_stats"] = {"success": True, "rows": len(stat_rows)}
    except Exception as exc:
        await _log_failure(engine, season, "usage_player_stats", exc)
        summary["sources"]["player_stats"] = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        stat_rows = []

    if not snap_rows:
        return summary

    stats_by_key = {(row.player_name, row.week): row for row in stat_rows}
    merged = []
    for row in snap_rows:
        if week is not None and row.week != week:
            continue
        stat_row = stats_by_key.get((row.player_name, row.week))
        if stat_row is not None:
            row.targets = stat_row.targets
            row.target_share = stat_row.target_share
            row.carries = stat_row.carries
            row.touches = stat_row.touches
        merged.append(row)

    by_week = {}
    for row in merged:
        by_week.setdefault(row.week, []).append(row)

    for week_num, week_rows in by_week.items():
        await engine.get_collection(PlayerWeekUsage).delete_many(
            {"season": season, "week": week_num}
        )
        await engine.save_all(week_rows)
        summary["weeks_replaced"].append(week_num)

    summary["weeks_replaced"].sort()
    return summary
