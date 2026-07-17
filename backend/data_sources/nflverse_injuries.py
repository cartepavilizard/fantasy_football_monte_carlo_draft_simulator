# -*- coding: utf-8 -*-
"""
NFLVERSE PRACTICE-REPORT INGESTION (PHASE D, TASK D2 — the cheap half)

Fills PracticeReport and InjuryDesignation from nflverse's public
`injuries_{season}.csv` release: the official NFL practice-participation
and game-status reports, aggregated league-wide. See
docs/specs/D2-practice-report-ingestion.md for the source decision
(nflverse over NFL.com/ESPN scraping) and the full write-semantics
contract; this module implements that spec verbatim, plus one mapping
update discovered while implementing it (below).

Styled exactly like NflverseUsageAdapter (data_sources/nflverse.py):
injectable Transport, RateLimiter(1.0), one GET per season file, never
raises past `ingest_practice_reports` — every failure mode degrades to
a logged LeagueSyncLog row (espn_league_id=None, league-independent
like sync_pro_schedule) with last-good data left in place.

MAPPING-TABLE DRIFT, FOUND AT IMPLEMENTATION TIME (2026-07-14): the
spec's column table assumed a `date_modified` timestamp column. The
actual current-season file (injuries_2025.csv, the most recent
completed season — 2026 hasn't started) does not carry one; nflverse
added `season_type` (redundant with `game_type`, unused here) and
dropped `date_modified` instead. This is exactly the mid-season/
cross-season format-drift scenario D2 was designed to survive — but
`date_modified` was never one of the required columns the header
tripwire checks (season, week, full_name, team are all still present),
so header validation correctly does not fire for it. Since
PracticeReport.report_date is a required field, the mapping falls back
to the ingest run's own date (midnight, i.e. day-granularity) when the
column is absent: reruns on the same calendar day upsert onto the same
key, and a new day starts a new trail row — reproducing the intended
Wed/Thu/Fri shape from ingestion cadence instead of the source's own
timestamp. If nflverse ever restores the column, it is parsed and
preferred (see `_report_date` below).
"""
import csv
import datetime
import io
from typing import Dict, List, Optional, Set, Tuple

from odmantic import query

from models.config import UNMAPPED_TRIPWIRE
from models.inseason import InjuryDesignation, LeagueSyncLog, PracticeReport, TeamWeekRoster
from models.notifications import ensure_notification

from .base import SourceFetchError
from .nfl_teams import normalize_team_abbrev
from .ratelimit import RateLimiter
from .transport import HttpxTransport, Transport

INJURIES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "injuries/injuries_{season}.csv"
)

# Column access is fail-soft per row (a missing/blank cell just skips
# the derived field); a missing column is a real schema break, caught
# here at header validation before any row is processed.
REQUIRED_COLUMNS = {"season", "week", "full_name", "team"}

PARTICIPATION_MAP = {
    "full participation in practice": "full",
    "limited participation in practice": "limited",
    "did not participate in practice": "dnp",
}
DESIGNATION_MAP = {
    "questionable": "questionable",
    "doubtful": "doubtful",
    "out": "out",
}

# full->limited, full->dnp, limited->dnp; a first-report dnp is handled
# separately (opening the week not practicing is itself news)
DOWNGRADE_PAIRS = {
    ("full", "limited"),
    ("full", "dnp"),
    ("limited", "dnp"),
}


class HeaderValidationError(RuntimeError):
    """Required columns missing from the CSV — a real schema break, not row noise"""


def _clean(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _report_date(
    row: dict, has_date_modified: bool, fallback: datetime.datetime
) -> datetime.datetime:
    """Prefer the source's own date_modified when present (historical
    files still carry it); otherwise the ingest run's own day — see the
    module docstring's drift note"""
    if has_date_modified:
        raw = (row.get("date_modified") or "").strip()
        if raw:
            try:
                return datetime.datetime.fromisoformat(raw)
            except ValueError:
                pass
    return fallback


class NflverseInjuriesAdapter:
    """One instance serves the injuries file; injectable Transport for tests"""

    min_request_interval_seconds = 1.0

    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or HttpxTransport()
        self._rate_limiter = RateLimiter(self.min_request_interval_seconds)

    async def fetch_injuries(
        self, season: int, now: Optional[datetime.datetime] = None
    ) -> Tuple[List[PracticeReport], List[InjuryDesignation], dict]:
        """
        One pass over injuries_{season}.csv: REG rows only, mapped
        through PARTICIPATION_MAP / DESIGNATION_MAP (lowercased,
        stripped match). Returns unsaved PracticeReport rows, unsaved
        InjuryDesignation rows, and stats for the caller's unmapped-
        value tripwire (`practice_non_blank`, `practice_unmapped`).
        Raises HeaderValidationError on a missing required column and
        SourceFetchError on an HTTP failure — both are the caller's
        job to catch, log, and degrade from.
        """
        now = now or datetime.datetime.now()
        await self._rate_limiter.wait()
        response = await self.transport.get(INJURIES_URL.format(season=season))
        if not response.ok:
            raise SourceFetchError(
                f"nflverse: GET injuries_{season}.csv returned {response.status_code}"
            )
        reader = csv.DictReader(io.StringIO(response.text))
        fieldnames = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - fieldnames
        if missing:
            raise HeaderValidationError(
                f"nflverse injuries_{season}.csv missing required columns: "
                f"{sorted(missing)}"
            )
        has_date_modified = "date_modified" in fieldnames
        fallback_report_date = datetime.datetime(now.year, now.month, now.day)

        reports: List[PracticeReport] = []
        designations: List[InjuryDesignation] = []
        practice_non_blank = 0
        practice_unmapped = 0

        for row in reader:
            if _clean(row.get("game_type")) != "reg":
                continue
            season_raw, week_raw = row.get("season"), row.get("week")
            player = (row.get("full_name") or "").strip()
            team = (row.get("team") or "").strip()
            if not season_raw or not week_raw or not player or not team:
                continue
            try:
                row_season = int(season_raw)
                week = int(week_raw)
            except ValueError:
                continue

            nfl_team = normalize_team_abbrev(team)
            position = (row.get("position") or "").strip() or None

            practice_key = _clean(row.get("practice_status"))
            if practice_key:
                practice_non_blank += 1
                participation = PARTICIPATION_MAP.get(practice_key)
                if participation is None:
                    practice_unmapped += 1
                else:
                    reports.append(
                        PracticeReport(
                            season=row_season,
                            week=week,
                            player_name=player,
                            nfl_team=nfl_team,
                            position=position,
                            report_date=_report_date(
                                row, has_date_modified, fallback_report_date
                            ),
                            participation=participation,
                            note=(row.get("practice_primary_injury") or "").strip()
                            or None,
                        )
                    )

            designation = DESIGNATION_MAP.get(_clean(row.get("report_status")))
            if designation is not None:
                designations.append(
                    InjuryDesignation(
                        season=row_season,
                        week=week,
                        player_name=player,
                        nfl_team=nfl_team,
                        position=position,
                        designation=designation,
                    )
                )

        stats = {
            "practice_non_blank": practice_non_blank,
            "practice_unmapped": practice_unmapped,
        }
        return reports, designations, stats


async def _log(
    engine,
    season: int,
    week: Optional[int],
    success: bool,
    error: Optional[str] = None,
    error_kind: Optional[str] = None,
) -> LeagueSyncLog:
    log = LeagueSyncLog(
        espn_league_id=None,
        season=season,
        section="practice_reports",
        week=week,
        success=success,
        error=error,
        error_kind=error_kind,
    )
    await engine.save(log)
    return log


def _is_downgrade(prev_participation: Optional[str], participation: str) -> bool:
    if prev_participation is None:
        return participation == "dnp"  # opening the week not practicing is news
    return (prev_participation, participation) in DOWNGRADE_PAIRS


async def _rostered_player_names(engine, season: int, week: int) -> Set[str]:
    """Players rostered in at least one synced league for this exact
    week — narrower than usage_shifts' season-wide filter: free agents'
    practice habits are the report view's job, not push material"""
    names: Set[str] = set()
    for roster in await engine.find(
        TeamWeekRoster, (TeamWeekRoster.season == season) & (TeamWeekRoster.week == week)
    ):
        names.update(entry.player_name for entry in roster.entries)
    return names


def _downgrade_copy(report: PracticeReport, prev: Optional[PracticeReport]) -> Tuple[str, str]:
    """C8-framed and factual — never speculates about availability;
    D3's notes and E1's curves do the interpreting"""
    name = report.player_name
    who_bits = [bit for bit in (report.nfl_team, report.position) if bit]
    who = f"{name} ({' '.join(who_bits)})" if who_bits else name
    day = report.report_date.strftime("%A")
    injury = f" ({report.note.lower()})" if report.note else ""
    if report.participation == "dnp":
        action, title = "did not practice", f"{name}: did not practice {day}"
    else:
        action, title = "was limited in practice", f"{name}: limited in practice {day}"
    if prev is None:
        body = (
            f"{who} {action} {day}{injury} — official report, ahead of any "
            "ESPN status change."
        )
    else:
        prev_day = prev.report_date.strftime("%A")
        body = (
            f"{who} {action} {day}{injury} after a {prev.participation} "
            f"{prev_day} — official report, ahead of any ESPN status change."
        )
    return title, body


async def ensure_practice_downgrade_notifications(
    engine, season: int, weeks: List[int]
) -> List:
    """
    Compare each rostered player's practice trail within each of `weeks`
    and raise deduped `practice_downgrade` notifications for full->
    limited, full->dnp, limited->dnp, and a first-report dnp. Never for
    upgrades or un-rostered players. Idempotent — dedupe_key is one per
    (player, week, participation level), so a same-level rerun (Wed dnp,
    Thu dnp) is a no-op, while a new level in the same week (dnp after
    an earlier limited alert) is a new event.
    """
    created = []
    for week in weeks:
        rostered = await _rostered_player_names(engine, season, week)
        if not rostered:
            continue
        reports = await engine.find(
            PracticeReport,
            (PracticeReport.season == season) & (PracticeReport.week == week),
            sort=query.asc(PracticeReport.report_date),
        )
        by_player: Dict[str, List[PracticeReport]] = {}
        for report in reports:
            if report.player_name in rostered:
                by_player.setdefault(report.player_name, []).append(report)

        for player_name, trail in by_player.items():
            prev: Optional[PracticeReport] = None
            for report in trail:
                if _is_downgrade(prev.participation if prev else None, report.participation):
                    title, body = _downgrade_copy(report, prev)
                    notification = await ensure_notification(
                        engine,
                        kind="practice_downgrade",
                        dedupe_key=(
                            f"practice:{season}:w{week}:{player_name}:"
                            f"{report.participation}"
                        ),
                        title=title,
                        body=body,
                        season=season,
                        week=week,
                    )
                    if notification is not None:
                        created.append(notification)
                prev = report
    return created


async def ingest_practice_reports(
    engine,
    season: int,
    week: Optional[int] = None,
    adapter: Optional[NflverseInjuriesAdapter] = None,
    now: Optional[datetime.datetime] = None,
) -> dict:
    """
    One ingestion pass: fetch injuries_{season}.csv once, filter to
    `week` when given (default: every week present in the file), apply
    D2's write semantics, log one sync-log row, then run the downgrade-
    alert pass. Never raises.

    Write semantics:
    - PracticeReport: daily-trail upsert keyed on (season, week,
      player_name, report_date) — the same practice day re-fetched
      updates in place; a new day inserts a new trail row. Prior days
      are never deleted.
    - InjuryDesignation: current-state replace per (season, week,
      player_name) — delete then insert, B1's pattern.

    Failure modes (all logged, never raised, last good data intact):
    - HTTP failure (season file 404 early in a season, etc.) ->
      error_kind="http".
    - Missing required column (a real schema break) ->
      error_kind="parse".
    - Unmapped practice_status values above UNMAPPED_TRIPWIRE ->
      error_kind="parse" plus an `ingest_format_change` notification;
      below the tripwire, unmapped rows are silently skipped.
    """
    adapter = adapter or NflverseInjuriesAdapter()
    summary = {
        "season": season,
        "week": week,
        "success": False,
        "reports_written": 0,
        "designations_written": 0,
        "weeks_touched": [],
    }

    try:
        reports, designations, stats = await adapter.fetch_injuries(season, now=now)
    except Exception as exc:
        error_kind = "http" if isinstance(exc, SourceFetchError) else "parse"
        error = f"{type(exc).__name__}: {exc}"
        await _log(engine, season, week, False, error, error_kind)
        summary["error"] = error
        return summary

    non_blank, unmapped = stats["practice_non_blank"], stats["practice_unmapped"]
    if non_blank > 0 and (unmapped / non_blank) > UNMAPPED_TRIPWIRE:
        error = (
            f"{unmapped}/{non_blank} practice_status values unmapped "
            f"(over the {UNMAPPED_TRIPWIRE:.0%} tripwire) — nflverse wording "
            "likely changed; PARTICIPATION_MAP needs a new entry"
        )
        await _log(engine, season, week, False, error, "parse")
        await ensure_notification(
            engine,
            kind="ingest_format_change",
            dedupe_key=f"nflverse_injuries:format:{season}:w{week}",
            title="Practice report wording changed upstream",
            body=(
                f"{unmapped} of {non_blank} practice-participation values from "
                "nflverse didn't match any known wording this week — the "
                "official source's format may have changed."
            ),
            season=season,
            week=week,
        )
        summary["error"] = error
        return summary

    if week is not None:
        reports = [report for report in reports if report.week == week]
        designations = [d for d in designations if d.week == week]

    weeks_touched = sorted({report.week for report in reports} | {d.week for d in designations})

    for report in reports:
        existing = await engine.find_one(
            PracticeReport,
            (PracticeReport.season == report.season)
            & (PracticeReport.week == report.week)
            & (PracticeReport.player_name == report.player_name)
            & (PracticeReport.report_date == report.report_date),
        )
        if existing is not None:
            existing.nfl_team = report.nfl_team
            existing.position = report.position
            existing.participation = report.participation
            existing.note = report.note
            existing.fetched_at = datetime.datetime.now()
            await engine.save(existing)
        else:
            await engine.save(report)
    summary["reports_written"] = len(reports)

    for designation in designations:
        await engine.get_collection(InjuryDesignation).delete_many(
            {
                "season": designation.season,
                "week": designation.week,
                "player_name": designation.player_name,
            }
        )
        await engine.save(designation)
    summary["designations_written"] = len(designations)

    summary["weeks_touched"] = weeks_touched
    summary["success"] = True
    await _log(engine, season, week, True)

    downgrades = await ensure_practice_downgrade_notifications(engine, season, weeks_touched)
    summary["downgrade_alerts"] = len(downgrades)
    return summary
