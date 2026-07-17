# -*- coding: utf-8 -*-
"""
ODMANTIC MODELS FOR IN-SEASON LEAGUE STATE (PHASE B, TASK B2)

Everything the in-season module knows about the user's real ESPN leagues
lives in these top-level collections — one document tree per league, per
scope, replaced idempotently by each sync (data_sources/espn_league.py).
Like models/sources.py, nothing here is embedded in the draft League
document: the Monte Carlo engine deep-copies League per iteration and
must never drag in-season state along.

Design constraints these models serve (read Phases C-F before changing):
- C1/C2 (lineup optimizer, matchup strength): TeamWeekRoster carries slot
  + projected/actual points per player-week; WeeklyMatchup carries the
  opponent graph and scores that matchup-strength math aggregates.
- C3 (K/DST streaming): FreeAgentSnapshot is the weekly available-player
  pool, position-filterable.
- C4 (usage shifts): PlayerWeekUsage is league-INDEPENDENT (snap counts
  and target shares are facts about the NFL, not about a fantasy league);
  B1 does not populate it — C4's ingestion does. The fields exist now so
  C4 writes into a stable schema.
- C5/C6/F2 (playoff SOS, lock strategy, byes): ProGame is the NFL
  schedule with kickoff times; week_lock_times() derives first/final
  lineup locks (B5's reminders and C6's early-lock logic read it).
- D2 (practice reports): PracticeReport (per practice day) and
  InjuryDesignation (per player-week game status) are separate models
  because they arrive on different cadences from different sources.
- E1-E8 (trades): LeagueTransaction keeps ESPN's transaction history
  queryable per owner/team across weeks (E3's willingness profiles);
  InSeasonLeague.trade_deadline feeds E8.
- B4 (perspective switcher): every read is served from these collections
  only; LeagueSyncLog + league_freshness() are how staleness is surfaced
  instead of triggering a fetch.

Failure-mode contract (shared with B1): syncs REPLACE a scope only after
a successful fetch+parse, so cookie expiry mid-season leaves the last
good data in place; LeagueSyncLog records every attempt (including
auth failures) and league_freshness() turns that into per-section
stale/auth_expired warnings that B4 attaches to every response.
"""
import datetime
from typing import Dict, List, Optional

from odmantic import EmbeddedModel, Model
from odmantic import Field as ODField
from odmantic import query

from .config import INSEASON_STALE_AFTER_HOURS

# Sync sections: each is fetched, persisted, and logged independently so
# one broken ESPN view degrades one section, not the whole league
SYNC_SECTIONS = [
    "league",
    "rosters",
    "matchups",
    "transactions",
    "free_agents",
    "pro_schedule",  # league-independent: logged with espn_league_id=None
    "practice_reports",  # league-independent (D2): logged with espn_league_id=None
]

# Sections logged with espn_league_id=None regardless of which league's
# envelope is asking — league_freshness() below must look them up the
# same way it looks up pro_schedule, not per-league
LEAGUE_INDEPENDENT_SECTIONS = {"pro_schedule", "practice_reports"}


# --- league + teams ----------------------------------------------------------


class LeagueTeamInfo(EmbeddedModel):
    """One fantasy team in one ESPN league, with its current record"""

    espn_team_id: int
    name: str
    abbrev: Optional[str] = None
    owner_guid: Optional[str] = None  # ESPN member GUID (joins owner profiles)
    owner_name: Optional[str] = None
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0


class InSeasonLeague(Model):
    """
    One ESPN league's settings + teams snapshot for one season — the
    perspective switcher's dropdown data and the anchor for every other
    in-season collection (all keyed by espn_league_id + season)
    """

    model_config = {"collection": "inseason_leagues"}

    espn_league_id: int
    season: int
    name: str
    team_count: int
    current_matchup_period: int = 1
    latest_scoring_period: int = 1  # ESPN's "current week" for stats
    final_scoring_period: Optional[int] = None
    trade_deadline: Optional[datetime.datetime] = None  # E8 reads this
    lineup_slot_counts: Dict[str, int] = {}  # slot name -> count, e.g. {"RB": 2}
    teams: List[LeagueTeamInfo] = []
    synced_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- rosters -----------------------------------------------------------------


class RosterSlotEntry(EmbeddedModel):
    """One player in one lineup slot for one week"""

    player_id: int  # ESPN player id (stable join key across collections)
    player_name: str
    position: Optional[str] = None  # QB/RB/WR/TE/K/DST
    nfl_team: Optional[str] = None
    lineup_slot: str  # QB/RB/WR/TE/FLEX/DST/K/BE/IR/...
    injury_status: Optional[str] = None  # espn's, lowercased (questionable/out/...)
    projected_points: Optional[float] = None  # this week, league scoring
    actual_points: Optional[float] = None


class TeamWeekRoster(Model):
    """One fantasy team's full roster for one scoring period"""

    model_config = {"collection": "inseason_rosters"}

    espn_league_id: int
    season: int
    week: int  # scoringPeriodId
    espn_team_id: int
    entries: List[RosterSlotEntry] = []
    synced_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- matchups ----------------------------------------------------------------


class WeeklyMatchup(Model):
    """
    One head-to-head matchup. week is ESPN's matchupPeriodId (playoff
    matchups can span multiple scoring periods; totals are ESPN's own).
    away_team_id is None on bye matchups.
    """

    model_config = {"collection": "inseason_matchups"}

    espn_league_id: int
    season: int
    week: int  # matchupPeriodId
    home_team_id: int
    away_team_id: Optional[int] = None
    home_points: float = 0.0
    away_points: float = 0.0
    winner: Optional[str] = None  # home | away | tie; None = undecided
    is_playoff: bool = False
    synced_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- transactions ------------------------------------------------------------


class TransactionItem(EmbeddedModel):
    """One player movement inside a transaction"""

    player_id: int
    player_name: Optional[str] = None  # resolved best-effort at sync time
    item_type: str  # ADD | DROP | TRADE (espn's item type, uppercased)
    from_team_id: Optional[int] = None
    to_team_id: Optional[int] = None


class LeagueTransaction(Model):
    """
    One executed/pending league transaction (waiver, free-agent move,
    trade). Pure lineup shuffles are filtered out at sync time — E3's
    willingness profiles and E-phase scanners only care about player
    movement between rosters and the pool.
    """

    model_config = {"collection": "inseason_transactions"}

    espn_league_id: int
    season: int
    espn_transaction_id: str  # ESPN's uuid; the idempotent upsert key
    type: str  # WAIVER | FREEAGENT | TRADE_ACCEPT | ... (espn's)
    status: str  # EXECUTED | PENDING | ...
    week: Optional[int] = None  # scoringPeriodId
    team_id: Optional[int] = None  # initiating team
    bid_amount: Optional[int] = None
    processed_at: Optional[datetime.datetime] = None
    items: List[TransactionItem] = []
    synced_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- free agents -------------------------------------------------------------


class FreeAgentEntry(EmbeddedModel):
    """One available player in the weekly free-agent snapshot"""

    player_id: int
    player_name: str
    position: Optional[str] = None
    nfl_team: Optional[str] = None
    injury_status: Optional[str] = None
    percent_owned: Optional[float] = None
    projected_points: Optional[float] = None  # this week, league scoring
    season_projection: Optional[float] = None


class FreeAgentSnapshot(Model):
    """
    The available-player pool for one league-week, one document per sync
    (batch-embedded like SourceRankingBatch: the pool is always read
    whole, then filtered in memory by position/name)
    """

    model_config = {"collection": "inseason_free_agents"}

    espn_league_id: int
    season: int
    week: int
    entries: List[FreeAgentEntry] = []
    synced_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- player-week usage (C4's schema; B1 does not populate) --------------------


class PlayerWeekUsage(Model):
    """
    League-independent weekly usage for one player: the volume/opportunity
    ground truth for C4's shift detection and C8's process-over-results
    framing. Snap counts and target shares are NOT in ESPN's league API —
    C4's ingestion (separate source, its own adapter) writes these rows;
    the model exists now so that schema is settled.
    """

    model_config = {"collection": "player_week_usage"}

    season: int
    week: int
    player_name: str
    player_id: Optional[int] = None  # ESPN id when resolvable
    position: Optional[str] = None
    nfl_team: Optional[str] = None
    opponent: Optional[str] = None
    snaps: Optional[int] = None
    snap_share: Optional[float] = None  # 0..1
    targets: Optional[int] = None
    target_share: Optional[float] = None  # 0..1
    routes: Optional[int] = None
    carries: Optional[int] = None
    touches: Optional[int] = None
    red_zone_touches: Optional[int] = None
    source: str = "usage_ingest"
    fetched_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- practice + injury (D2's schema; B1 only fills espn injury_status) --------


class PracticeReport(Model):
    """One player's participation on one practice day (D2's early signal)"""

    model_config = {"collection": "practice_reports"}

    season: int
    week: int
    player_name: str
    nfl_team: Optional[str] = None
    position: Optional[str] = None
    report_date: datetime.datetime
    participation: str  # full | limited | dnp
    note: Optional[str] = None
    source: str = "official_report"
    fetched_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


class InjuryDesignation(Model):
    """One player's official game-status designation for one week"""

    model_config = {"collection": "injury_designations"}

    season: int
    week: int
    player_name: str
    nfl_team: Optional[str] = None
    position: Optional[str] = None
    designation: str  # questionable | doubtful | out | ir | pup | active
    source: str = "official_report"
    updated_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- NFL schedule / lineup locks ----------------------------------------------


class ProGame(Model):
    """
    One real NFL game with its kickoff time — lineup lock for every
    player in it. week is the scoringPeriodId the game counts toward.
    """

    model_config = {"collection": "pro_games"}

    season: int
    week: int
    espn_game_id: Optional[int] = None
    home_team: str  # abbreviation, e.g. SEA
    away_team: str
    kickoff: datetime.datetime
    synced_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


def week_lock_times(games: List[ProGame]) -> Optional[dict]:
    """
    First/final lineup locks for one week's games, plus per-NFL-team lock
    times. First lock is simply the earliest kickoff — which is exactly
    how the Wednesday season opener stays covered without a special case.
    Returns None when there are no games to reason about.
    """
    if not games:
        return None
    by_team = {}
    for game in games:
        for team in (game.home_team, game.away_team):
            existing = by_team.get(team)
            if existing is None or game.kickoff < existing:
                by_team[team] = game.kickoff
    ordered = sorted(games, key=lambda game: game.kickoff)
    return {
        "first_lock": ordered[0].kickoff,
        "final_lock": ordered[-1].kickoff,
        "first_game": f"{ordered[0].away_team} @ {ordered[0].home_team}",
        "team_locks": by_team,
    }


# --- sync log + freshness -----------------------------------------------------


class LeagueSyncLog(Model):
    """
    One record per attempted sync of one section — the raw material for
    league_freshness(). Auth failures get error_kind='auth' so cookie
    expiry is distinguishable from ESPN being down.
    """

    model_config = {"collection": "league_sync_log"}

    espn_league_id: Optional[int] = None  # None for league-independent sections
    season: int
    section: str  # one of SYNC_SECTIONS
    week: Optional[int] = None
    success: bool = True
    error: Optional[str] = None
    error_kind: Optional[str] = None  # auth | http | parse | skipped
    fetched_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


async def _latest_log(
    engine, espn_league_id, season, section, successful_only=False
) -> Optional[LeagueSyncLog]:
    criteria = (
        (LeagueSyncLog.espn_league_id == espn_league_id)
        & (LeagueSyncLog.season == season)
        & (LeagueSyncLog.section == section)
    )
    if successful_only:
        criteria = criteria & (LeagueSyncLog.success == True)  # noqa: E712
    return await engine.find_one(
        LeagueSyncLog,
        criteria,
        # fetched_at is ms-truncated so back-to-back logs tie; id breaks
        # toward the newest (same pattern as latest_batch)
        sort=(query.desc(LeagueSyncLog.fetched_at), query.desc(LeagueSyncLog.id)),
    )


async def league_freshness(
    engine,
    espn_league_id: int,
    season: int,
    now: Optional[datetime.datetime] = None,
    stale_after_hours: Optional[float] = None,
) -> dict:
    """
    Per-section freshness for one league: when each section last synced,
    whether it is stale, and whether the latest attempt failed auth.
    B4 attaches this to EVERY in-season response — cached data is always
    served, and this is what keeps stale from masquerading as fresh.
    """
    now = now or datetime.datetime.now()
    if stale_after_hours is None:
        stale_after_hours = INSEASON_STALE_AFTER_HOURS
    stale_after_seconds = stale_after_hours * 3600

    sections = {}
    any_stale = False
    auth_expired = False
    warnings = []
    for section in SYNC_SECTIONS:
        # league-independent sections carry no league id on their logs
        league_key = None if section in LEAGUE_INDEPENDENT_SECTIONS else espn_league_id
        last_attempt = await _latest_log(engine, league_key, season, section)
        last_success = (
            last_attempt
            if last_attempt is not None and last_attempt.success
            else await _latest_log(
                engine, league_key, season, section, successful_only=True
            )
        )
        age_seconds = (
            round((now - last_success.fetched_at).total_seconds())
            if last_success
            else None
        )
        stale = age_seconds is None or age_seconds > stale_after_seconds
        section_auth = (
            last_attempt is not None
            and not last_attempt.success
            and last_attempt.error_kind == "auth"
        )
        sections[section] = {
            "last_success_at": (
                last_success.fetched_at.isoformat() if last_success else None
            ),
            "last_attempt_at": (
                last_attempt.fetched_at.isoformat() if last_attempt else None
            ),
            "last_error": (
                last_attempt.error
                if last_attempt is not None and not last_attempt.success
                else None
            ),
            "error_kind": (
                last_attempt.error_kind
                if last_attempt is not None and not last_attempt.success
                else None
            ),
            "age_seconds": age_seconds,
            "stale": stale,
        }
        any_stale = any_stale or stale
        if section_auth:
            auth_expired = True
        if section_auth and last_success:
            warnings.append(
                f"ESPN cookies appear expired ({section} sync failed "
                f"authentication); showing cached data from "
                f"{last_success.fetched_at.isoformat()}"
            )
        elif section_auth:
            warnings.append(
                f"ESPN cookies appear expired ({section} sync failed "
                "authentication) and no cached data exists yet"
            )
        elif stale and last_success:
            warnings.append(
                f"{section} data is stale (last successful sync "
                f"{round(age_seconds / 3600, 1)}h ago)"
            )
        elif last_success is None:
            warnings.append(f"{section} has never synced for this league")

    return {
        "espn_league_id": espn_league_id,
        "season": season,
        "sections": sections,
        "stale": any_stale,
        "auth_expired": auth_expired,
        "warnings": warnings,
    }
