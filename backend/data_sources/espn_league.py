# -*- coding: utf-8 -*-
"""
ESPN IN-SEASON LEAGUE ADAPTER (PHASE B, TASK B1)

Authenticated reads of the user's real ESPN leagues: settings/teams,
weekly rosters, matchups/scores, transactions, free agents, and the NFL
schedule (lineup-lock times). Direct HTTP against the same lm-api-reads
views ESPN's own app uses, through the shared Transport/RateLimiter
seams — espn-api stays confined to historical draft ingestion
(espn_history.py); in-season sync needs per-view failure isolation and
scripted-transport testability that the library doesn't give us.

Auth: private leagues need the espn_s2/SWID cookie pair (env-only, from
models/config.py — never stored in Mongo). Failure modes are the point
of this module's design:

- A 401/403 raises EspnAuthError, logged with error_kind='auth' so
  cookie expiry mid-season is distinguishable from ESPN outages.
- Each section (league, rosters, matchups, transactions, free_agents,
  pro_schedule) is fetched, parsed, and persisted INDEPENDENTLY; one
  broken view degrades one section.
- A scope in Mongo is only replaced after its fetch+parse succeeded —
  a failing sync leaves the last good data in place. Combined with
  league_freshness() (models/inseason.py), that means cookie expiry
  degrades to clearly-stale cached data with a visible warning, never
  a crash and never stale-served-as-fresh.

No raw-response caching here: Mongo IS the cache for in-season data,
and a 6h-TTL disk cache would hide fresh scores on game day.
"""
from datetime import datetime
import json
from typing import Dict, List, Optional

from models.config import (
    DRAFT_YEAR,
    ESPN_LEAGUE_IDS,
    ESPN_S2,
    ESPN_SWID,
    FREE_AGENT_FETCH_LIMIT,
)
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InSeasonLeague,
    LeagueSyncLog,
    LeagueTeamInfo,
    LeagueTransaction,
    ProGame,
    RosterSlotEntry,
    TeamWeekRoster,
    TransactionItem,
    WeeklyMatchup,
)

from .base import SourceFetchError
from .espn_rankings import POSITION_IDS, PRO_TEAM_IDS
from .ratelimit import RateLimiter
from .transport import HttpxTransport, Transport

LEAGUE_URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leagues/{league_id}"
)
SEASON_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"

LINEUP_SLOT_IDS = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 16: "DST", 17: "K", 20: "BE", 21: "IR", 23: "FLEX",
}

# Transaction item types that are player movement (vs. lineup shuffles,
# which E-phase consumers never care about)
MOVEMENT_ITEM_TYPES = {"ADD", "DROP", "TRADE"}


class EspnAuthError(SourceFetchError):
    """ESPN rejected the espn_s2/SWID cookies (expired, wrong, or missing)"""


def _epoch_ms(value) -> Optional[datetime]:
    """ESPN timestamps are epoch milliseconds; 0/None mean unset"""
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000)


def _member_names(payload: dict) -> Dict[str, str]:
    """{member guid -> display name} from a mTeam response"""
    names = {}
    for member in payload.get("members", []) or []:
        name = " ".join(
            part
            for part in [member.get("firstName"), member.get("lastName")]
            if part
        ) or member.get("displayName")
        if member.get("id") and name:
            names[member["id"]] = name
    return names


def _team_name(team: dict) -> str:
    if team.get("name"):
        return team["name"]
    return " ".join(
        part for part in [team.get("location"), team.get("nickname")] if part
    ) or f"Team {team.get('id')}"


def _week_points(player: dict, week: int, stat_source_id: int) -> Optional[float]:
    """appliedTotal for one scoring period; source 0 = actual, 1 = projected"""
    for stat in player.get("stats", []) or []:
        if (
            stat.get("scoringPeriodId") == week
            and stat.get("statSourceId") == stat_source_id
        ):
            return stat.get("appliedTotal")
    return None


def _season_projection(player: dict, season: int) -> Optional[float]:
    for stat in player.get("stats", []) or []:
        if (
            stat.get("seasonId") == season
            and stat.get("statSourceId") == 1
            and stat.get("statSplitTypeId") == 0
        ):
            return stat.get("appliedTotal")
    return None


class EspnLeagueAdapter:
    """
    One instance serves all three leagues (the rate limiter is per-host,
    not per-league). Transport is injectable exactly like the ranking
    adapters, so tests script it; cookies default from env config.
    """

    min_request_interval_seconds = 1.0

    def __init__(
        self,
        espn_s2: Optional[str] = None,
        swid: Optional[str] = None,
        transport: Optional[Transport] = None,
    ):
        self.espn_s2 = espn_s2 if espn_s2 is not None else ESPN_S2
        self.swid = swid if swid is not None else ESPN_SWID
        self.transport = transport or HttpxTransport()
        self._rate_limiter = RateLimiter(self.min_request_interval_seconds)

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = dict(extra or {})
        if self.espn_s2 and self.swid:
            headers["Cookie"] = f"espn_s2={self.espn_s2}; SWID={self.swid}"
        return headers

    async def _get(self, url, *, params=None, headers=None) -> dict:
        await self._rate_limiter.wait()
        response = await self.transport.get(
            url, params=params, headers=self._headers(headers)
        )
        if response.status_code in (401, 403):
            raise EspnAuthError(
                f"espn_league: GET {url} returned {response.status_code} — "
                "espn_s2/SWID cookies are missing, wrong, or expired"
            )
        if not response.ok:
            raise SourceFetchError(
                f"espn_league: GET {url} returned {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise SourceFetchError(f"espn_league: GET {url} returned non-JSON ({exc})")

    # -- section fetch+parse (each returns unsaved model instances) ----------

    async def fetch_league(self, espn_league_id: int, season: int) -> InSeasonLeague:
        payload = await self._get(
            LEAGUE_URL.format(season=season, league_id=espn_league_id),
            params={"view": ["mTeam", "mSettings"]},
        )
        settings = payload.get("settings") or {}
        status = payload.get("status") or {}
        member_names = _member_names(payload)
        teams = []
        for team in payload.get("teams", []) or []:
            owners = team.get("owners") or []
            owner_guid = owners[0] if owners else None
            record = ((team.get("record") or {}).get("overall")) or {}
            teams.append(
                LeagueTeamInfo(
                    espn_team_id=team["id"],
                    name=_team_name(team),
                    abbrev=team.get("abbrev"),
                    owner_guid=owner_guid,
                    owner_name=member_names.get(owner_guid),
                    wins=record.get("wins", 0) or 0,
                    losses=record.get("losses", 0) or 0,
                    ties=record.get("ties", 0) or 0,
                    points_for=record.get("pointsFor", 0.0) or 0.0,
                    points_against=record.get("pointsAgainst", 0.0) or 0.0,
                )
            )
        if not teams:
            raise SourceFetchError(
                f"espn_league {espn_league_id}: response contained no teams"
            )
        slot_counts = {}
        roster_settings = settings.get("rosterSettings") or {}
        for slot_id, count in (roster_settings.get("lineupSlotCounts") or {}).items():
            if count:
                slot_counts[LINEUP_SLOT_IDS.get(int(slot_id), str(slot_id))] = count
        trade_deadline = _epoch_ms(
            (settings.get("tradeSettings") or {}).get("deadlineDate")
        )
        return InSeasonLeague(
            espn_league_id=espn_league_id,
            season=season,
            name=settings.get("name") or f"League {espn_league_id}",
            team_count=len(teams),
            current_matchup_period=status.get("currentMatchupPeriod", 1) or 1,
            latest_scoring_period=status.get("latestScoringPeriod", 1) or 1,
            final_scoring_period=status.get("finalScoringPeriod"),
            trade_deadline=trade_deadline,
            lineup_slot_counts=slot_counts,
            teams=teams,
        )

    async def fetch_rosters(
        self, espn_league_id: int, season: int, week: int
    ) -> List[TeamWeekRoster]:
        payload = await self._get(
            LEAGUE_URL.format(season=season, league_id=espn_league_id),
            params={"view": "mRoster", "scoringPeriodId": week},
        )
        rosters = []
        for team in payload.get("teams", []) or []:
            entries = []
            for entry in ((team.get("roster") or {}).get("entries")) or []:
                pool_entry = entry.get("playerPoolEntry") or {}
                player = pool_entry.get("player") or {}
                if not player.get("fullName"):
                    continue
                entries.append(
                    RosterSlotEntry(
                        player_id=player.get("id", 0) or 0,
                        player_name=player["fullName"],
                        position=POSITION_IDS.get(player.get("defaultPositionId")),
                        nfl_team=PRO_TEAM_IDS.get(player.get("proTeamId")),
                        lineup_slot=LINEUP_SLOT_IDS.get(
                            entry.get("lineupSlotId"), str(entry.get("lineupSlotId"))
                        ),
                        injury_status=(player.get("injuryStatus") or "").lower()
                        or None,
                        projected_points=_week_points(player, week, 1),
                        actual_points=_week_points(player, week, 0),
                    )
                )
            rosters.append(
                TeamWeekRoster(
                    espn_league_id=espn_league_id,
                    season=season,
                    week=week,
                    espn_team_id=team["id"],
                    entries=entries,
                )
            )
        if not rosters:
            raise SourceFetchError(
                f"espn_league {espn_league_id}: mRoster returned no teams"
            )
        return rosters

    async def fetch_matchups(
        self, espn_league_id: int, season: int
    ) -> List[WeeklyMatchup]:
        payload = await self._get(
            LEAGUE_URL.format(season=season, league_id=espn_league_id),
            params={"view": "mMatchup"},
        )
        winner_map = {"HOME": "home", "AWAY": "away", "TIE": "tie"}
        matchups = []
        for entry in payload.get("schedule", []) or []:
            home = entry.get("home") or {}
            away = entry.get("away") or {}
            if "teamId" not in home:
                continue
            matchups.append(
                WeeklyMatchup(
                    espn_league_id=espn_league_id,
                    season=season,
                    week=entry.get("matchupPeriodId", 0) or 0,
                    home_team_id=home["teamId"],
                    away_team_id=away.get("teamId"),
                    home_points=home.get("totalPoints", 0.0) or 0.0,
                    away_points=away.get("totalPoints", 0.0) or 0.0,
                    winner=winner_map.get(entry.get("winner")),
                    is_playoff=(entry.get("playoffTierType") or "NONE") != "NONE",
                )
            )
        if not matchups:
            raise SourceFetchError(
                f"espn_league {espn_league_id}: mMatchup returned no schedule"
            )
        return matchups

    async def fetch_transactions(
        self,
        espn_league_id: int,
        season: int,
        week: int,
        player_names: Optional[Dict[int, str]] = None,
    ) -> List[LeagueTransaction]:
        """
        Transactions visible for one scoring period. mTransactions2 items
        carry only player ids; names are resolved best-effort from the
        map built during the same sync pass (rosters + free agents).
        Returns [] when the league simply has no transactions yet.
        """
        payload = await self._get(
            LEAGUE_URL.format(season=season, league_id=espn_league_id),
            params={"view": "mTransactions2", "scoringPeriodId": week},
        )
        player_names = player_names or {}
        transactions = []
        for entry in payload.get("transactions", []) or []:
            items = []
            for item in entry.get("items", []) or []:
                item_type = (item.get("type") or "").upper()
                if item_type not in MOVEMENT_ITEM_TYPES:
                    continue
                player_id = item.get("playerId", 0) or 0
                items.append(
                    TransactionItem(
                        player_id=player_id,
                        player_name=player_names.get(player_id),
                        item_type=item_type,
                        from_team_id=item.get("fromTeamId"),
                        to_team_id=item.get("toTeamId"),
                    )
                )
            if not items:
                continue  # pure lineup shuffle
            transactions.append(
                LeagueTransaction(
                    espn_league_id=espn_league_id,
                    season=season,
                    espn_transaction_id=str(entry.get("id")),
                    type=entry.get("type") or "UNKNOWN",
                    status=entry.get("status") or "UNKNOWN",
                    week=entry.get("scoringPeriodId"),
                    team_id=entry.get("teamId"),
                    bid_amount=entry.get("bidAmount"),
                    processed_at=_epoch_ms(
                        entry.get("processDate") or entry.get("proposedDate")
                    ),
                    items=items,
                )
            )
        return transactions

    async def fetch_free_agents(
        self,
        espn_league_id: int,
        season: int,
        week: int,
        limit: int = FREE_AGENT_FETCH_LIMIT,
    ) -> FreeAgentSnapshot:
        fantasy_filter = {
            "players": {
                "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
                "limit": limit,
                "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            }
        }
        payload = await self._get(
            LEAGUE_URL.format(season=season, league_id=espn_league_id),
            params={"view": "kona_player_info", "scoringPeriodId": week},
            headers={"X-Fantasy-Filter": json.dumps(fantasy_filter)},
        )
        entries = []
        for wrapper in payload.get("players", []) or []:
            player = wrapper.get("player") or {}
            if not player.get("fullName"):
                continue
            ownership = player.get("ownership") or {}
            entries.append(
                FreeAgentEntry(
                    player_id=player.get("id", 0) or 0,
                    player_name=player["fullName"],
                    position=POSITION_IDS.get(player.get("defaultPositionId")),
                    nfl_team=PRO_TEAM_IDS.get(player.get("proTeamId")),
                    injury_status=(player.get("injuryStatus") or "").lower() or None,
                    percent_owned=ownership.get("percentOwned"),
                    projected_points=_week_points(player, week, 1),
                    season_projection=_season_projection(player, season),
                )
            )
        if not entries:
            raise SourceFetchError(
                f"espn_league {espn_league_id}: free agent pool came back empty"
            )
        return FreeAgentSnapshot(
            espn_league_id=espn_league_id, season=season, week=week, entries=entries
        )

    async def fetch_pro_schedule(self, season: int) -> List[ProGame]:
        """The NFL schedule with kickoffs — lineup-lock times (league-free)"""
        payload = await self._get(
            SEASON_URL.format(season=season),
            params={"view": "proTeamSchedules_wl"},
        )
        pro_teams = ((payload.get("settings") or {}).get("proTeams")) or []
        games = {}
        for team in pro_teams:
            by_period = team.get("proGamesByScoringPeriod") or {}
            for week, week_games in by_period.items():
                for game in week_games or []:
                    game_id = game.get("id")
                    if game_id in games:
                        continue  # each game appears under both teams
                    kickoff = _epoch_ms(game.get("date"))
                    home = PRO_TEAM_IDS.get(game.get("homeProTeamId"))
                    away = PRO_TEAM_IDS.get(game.get("awayProTeamId"))
                    if kickoff is None or home is None or away is None:
                        continue
                    games[game_id] = ProGame(
                        season=season,
                        week=int(week),
                        espn_game_id=game_id,
                        home_team=home,
                        away_team=away,
                        kickoff=kickoff,
                    )
        if not games:
            raise SourceFetchError(
                "espn_league: pro schedule response contained no games"
            )
        return list(games.values())


# --- sync orchestration ---------------------------------------------------------


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, EspnAuthError):
        return "auth"
    if isinstance(exc, SourceFetchError):
        return "http"
    return "parse"


async def _log(
    engine, espn_league_id, season, section, week=None, error: Exception = None,
    error_kind: str = None,
):
    log = LeagueSyncLog(
        espn_league_id=espn_league_id,
        season=season,
        section=section,
        week=week,
        success=error is None and error_kind is None,
        error=f"{type(error).__name__}: {error}" if error else None,
        error_kind=_error_kind(error) if error else error_kind,
    )
    await engine.save(log)
    return log


async def sync_league(
    engine,
    espn_league_id: int,
    season: int = DRAFT_YEAR,
    week: Optional[int] = None,
    adapter: Optional[EspnLeagueAdapter] = None,
    free_agent_limit: int = FREE_AGENT_FETCH_LIMIT,
) -> dict:
    """
    One full sync pass for one league. Sections run independently; each
    replaces its Mongo scope only on success and always writes a
    LeagueSyncLog row. Never raises for a failing section — the summary
    (and league_freshness) carry the outcome. The pro schedule is synced
    separately (sync_pro_schedule) since it is league-independent.
    """
    adapter = adapter or EspnLeagueAdapter()
    sections = {}

    # league settings first: it defines the current week for the others
    league_doc = None
    try:
        league_doc = await adapter.fetch_league(espn_league_id, season)
        await engine.get_collection(InSeasonLeague).delete_many(
            {"espn_league_id": espn_league_id, "season": season}
        )
        await engine.save(league_doc)
        await _log(engine, espn_league_id, season, "league")
        sections["league"] = {"success": True, "teams": len(league_doc.teams)}
    except Exception as exc:
        log = await _log(engine, espn_league_id, season, "league", error=exc)
        sections["league"] = {
            "success": False, "error": log.error, "error_kind": log.error_kind,
        }

    if week is None:
        # degrade to the cached league doc so a failing settings view
        # doesn't take the week-scoped sections down with it
        if league_doc is None:
            league_doc = await engine.find_one(
                InSeasonLeague,
                (InSeasonLeague.espn_league_id == espn_league_id)
                & (InSeasonLeague.season == season),
            )
        week = league_doc.latest_scoring_period if league_doc else None

    if week is None:
        for section in ["rosters", "matchups", "transactions", "free_agents"]:
            await _log(
                engine, espn_league_id, season, section, error_kind="skipped"
            )
            sections[section] = {
                "success": False,
                "error": "current week unknown (league settings never synced)",
                "error_kind": "skipped",
            }
        return {
            "espn_league_id": espn_league_id, "season": season, "week": None,
            "sections": sections,
        }

    # rosters + free agents also feed the player-id -> name map that
    # makes transaction items human-readable
    player_names: Dict[int, str] = {}

    # Backfill the just-completed week's rosters first (C2 reads actual
    # points per completed week; without this, a week's stored actuals
    # freeze at whatever the last sync DURING that week saw — usually
    # missing Sunday/Monday-night finals). Logged under the same
    # "rosters" section; the current-week fetch below logs after it, so
    # freshness always reflects the current week's outcome.
    if week > 1:
        try:
            previous = await adapter.fetch_rosters(espn_league_id, season, week - 1)
            await engine.get_collection(TeamWeekRoster).delete_many(
                {
                    "espn_league_id": espn_league_id,
                    "season": season,
                    "week": week - 1,
                }
            )
            await engine.save_all(previous)
            await _log(engine, espn_league_id, season, "rosters", week=week - 1)
        except Exception as exc:
            await _log(
                engine, espn_league_id, season, "rosters", week=week - 1, error=exc
            )

    try:
        rosters = await adapter.fetch_rosters(espn_league_id, season, week)
        await engine.get_collection(TeamWeekRoster).delete_many(
            {"espn_league_id": espn_league_id, "season": season, "week": week}
        )
        await engine.save_all(rosters)
        await _log(engine, espn_league_id, season, "rosters", week=week)
        for roster in rosters:
            for entry in roster.entries:
                player_names[entry.player_id] = entry.player_name
        sections["rosters"] = {"success": True, "teams": len(rosters)}
    except Exception as exc:
        log = await _log(engine, espn_league_id, season, "rosters", week=week, error=exc)
        sections["rosters"] = {
            "success": False, "error": log.error, "error_kind": log.error_kind,
        }

    try:
        matchups = await adapter.fetch_matchups(espn_league_id, season)
        await engine.get_collection(WeeklyMatchup).delete_many(
            {"espn_league_id": espn_league_id, "season": season}
        )
        await engine.save_all(matchups)
        await _log(engine, espn_league_id, season, "matchups")
        sections["matchups"] = {"success": True, "matchups": len(matchups)}
    except Exception as exc:
        log = await _log(engine, espn_league_id, season, "matchups", error=exc)
        sections["matchups"] = {
            "success": False, "error": log.error, "error_kind": log.error_kind,
        }

    try:
        snapshot = await adapter.fetch_free_agents(
            espn_league_id, season, week, limit=free_agent_limit
        )
        await engine.get_collection(FreeAgentSnapshot).delete_many(
            {"espn_league_id": espn_league_id, "season": season, "week": week}
        )
        await engine.save(snapshot)
        await _log(engine, espn_league_id, season, "free_agents", week=week)
        for entry in snapshot.entries:
            player_names.setdefault(entry.player_id, entry.player_name)
        sections["free_agents"] = {"success": True, "players": len(snapshot.entries)}
    except Exception as exc:
        log = await _log(
            engine, espn_league_id, season, "free_agents", week=week, error=exc
        )
        sections["free_agents"] = {
            "success": False, "error": log.error, "error_kind": log.error_kind,
        }

    try:
        transactions = await adapter.fetch_transactions(
            espn_league_id, season, week, player_names=player_names
        )
        if transactions:
            await engine.get_collection(LeagueTransaction).delete_many(
                {
                    "espn_league_id": espn_league_id,
                    "season": season,
                    "espn_transaction_id": {
                        "$in": [t.espn_transaction_id for t in transactions]
                    },
                }
            )
            await engine.save_all(transactions)
        await _log(engine, espn_league_id, season, "transactions", week=week)
        sections["transactions"] = {"success": True, "transactions": len(transactions)}
    except Exception as exc:
        log = await _log(
            engine, espn_league_id, season, "transactions", week=week, error=exc
        )
        sections["transactions"] = {
            "success": False, "error": log.error, "error_kind": log.error_kind,
        }

    return {
        "espn_league_id": espn_league_id,
        "season": season,
        "week": week,
        "sections": sections,
    }


async def sync_pro_schedule(
    engine, season: int = DRAFT_YEAR, adapter: Optional[EspnLeagueAdapter] = None
) -> dict:
    """League-independent NFL schedule sync (lineup-lock times)"""
    adapter = adapter or EspnLeagueAdapter()
    try:
        games = await adapter.fetch_pro_schedule(season)
        await engine.get_collection(ProGame).delete_many({"season": season})
        await engine.save_all(games)
        await _log(engine, None, season, "pro_schedule")
        return {"success": True, "games": len(games)}
    except Exception as exc:
        log = await _log(engine, None, season, "pro_schedule", error=exc)
        return {"success": False, "error": log.error, "error_kind": log.error_kind}


async def sync_all_leagues(
    engine,
    season: int = DRAFT_YEAR,
    week: Optional[int] = None,
    league_ids: Optional[List[int]] = None,
    adapter: Optional[EspnLeagueAdapter] = None,
) -> dict:
    """
    One pass over every configured league plus the shared pro schedule —
    what the on-demand endpoint and B3's scheduled loop both call.
    """
    league_ids = league_ids if league_ids is not None else ESPN_LEAGUE_IDS
    adapter = adapter or EspnLeagueAdapter()
    summary = {
        "season": season,
        "pro_schedule": await sync_pro_schedule(engine, season, adapter=adapter),
        "leagues": {},
    }
    for league_id in league_ids:
        summary["leagues"][league_id] = await sync_league(
            engine, league_id, season=season, week=week, adapter=adapter
        )
    return summary
