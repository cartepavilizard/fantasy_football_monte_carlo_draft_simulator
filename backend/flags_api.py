# -*- coding: utf-8 -*-
"""
F1/F2/F3 STRATEGY-FLAGS ENDPOINTS (PHASE F)

A small APIRouter exposing the three display-only awareness features
built in models/correlation_flags.py (F1 stacking + F3 anti-correlation)
and models/bye_planning.py (F2 bye clustering + thin-week preview).

Pattern-matches notifications_api.py / inseason_api.py:
  - configure(engine_getter) binds a late-bound engine so tests that
    swap app.engine (conftest) are honored automatically;
  - every read is Mongo-only — this module imports nothing from
    data_sources, directly or transitively (its whole import closure
    is models/correlation_flags, models/bye_planning, models/handcuffs,
    models/inseason, models/config, all of which are cached-only);
  - responses carry the standard `freshness` + `warnings` envelope from
    league_freshness(), so cached data degrades visibly rather than
    masquerading as fresh.

FLAGS, NEVER RULES: removing every flag these endpoints return changes
NO ranking, valuation, verdict, or projection (spec F1 §7; F3 inherits).
They decorate rosters with awareness only.

Router prefix is /inseason so it joins the existing in-season read path
namespace. app.py includes this router alongside inseason_api's; the
endpoints are deliberately distinct paths so they can be added/removed
without touching inseason_api.py.
"""
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException

from models.bye_planning import (
    BYE_CLUSTER_THRESHOLD,
    bye_cluster_warning,
    thin_week_preview,
)
from models.config import DRAFT_YEAR
from models.correlation_flags import (
    anticorrelation_flags,
    roster_stack_flags,
)
from models.handcuffs import list_handcuffs
from models.inseason import (
    InSeasonLeague,
    ProGame,
    TeamWeekRoster,
    league_freshness,
)

router = APIRouter(prefix="/inseason", tags=["strategy-flags"])

_engine_getter: Optional[Callable] = None


def configure(engine_getter: Callable):
    """Bind the late-bound engine getter (mirrors notifications_api /
    inseason_api). app.py calls this with `lambda: engine` at startup."""
    global _engine_getter
    _engine_getter = engine_getter


def _engine():
    if _engine_getter is None:
        raise RuntimeError("flags_api.configure() was never called")
    return _engine_getter()


async def _league_or_404(
    engine, espn_league_id: int, season: int
) -> InSeasonLeague:
    league = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if league is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"ESPN league {espn_league_id} has no synced data for "
                f"{season}; run POST /inseason/sync first"
            ),
        )
    return league


async def _envelope(engine, espn_league_id: int, season: int, data) -> dict:
    freshness = await league_freshness(engine, espn_league_id, season)
    return {
        "data": data,
        "freshness": freshness,
        "warnings": freshness["warnings"],
    }


def _roster_view(roster: TeamWeekRoster) -> dict:
    """Project a TeamWeekRoster into the plain-dict shape the pure flag
    functions read (read-only — the model is never mutated)."""
    return {
        "espn_team_id": roster.espn_team_id,
        "week": roster.week,
        "season": roster.season,
        "entries": [
            {
                "name": entry.player_name,
                "player_name": entry.player_name,
                "position": entry.position,
                "nfl_team": entry.nfl_team,
                "lineup_slot": entry.lineup_slot,
                "weekly_projection": entry.projected_points,
                "projected_points": entry.projected_points,
                "injury_status": entry.injury_status,
            }
            for entry in roster.entries
        ],
    }


async def _handcuff_exclusion(engine) -> frozenset:
    """Active C7 starter->handcuff name-pairs, as the F3 exclusion set
    (those are insurance, not competition)."""
    pairs = await list_handcuffs(engine)
    return frozenset(
        frozenset({p.starter_name, p.handcuff_name}) for p in pairs
    )


@router.get("/league/{espn_league_id}/strategy_flags")
async def get_strategy_flags(
    espn_league_id: int,
    espn_team_id: Optional[int] = None,
    season: int = DRAFT_YEAR,
    week: Optional[int] = None,
):
    """
    F1 stacking + F3 anti-correlation flags for one roster (or every
    roster in the league when espn_team_id is omitted). Display-only.

    Returns, per roster:
      stacks:           [F1 flags] — best same-NFL-team QB/pass-catcher
                         pairing per rostered player, with extra_swing
      anti_correlation: [F3 flags] — same-backfield RB pairs NOT in the
                         C7 handcuff table (committee competition)
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    if week is None:
        week = league.latest_scoring_period

    conditions = [
        TeamWeekRoster.espn_league_id == espn_league_id,
        TeamWeekRoster.season == season,
        TeamWeekRoster.week == week,
    ]
    if espn_team_id is not None:
        conditions.append(TeamWeekRoster.espn_team_id == espn_team_id)
    criteria = {}
    for condition in conditions:
        criteria = condition if criteria == {} else criteria & condition
    rosters = await engine.find(TeamWeekRoster, criteria)
    rosters = sorted(rosters, key=lambda r: r.espn_team_id)

    exclusion = await _handcuff_exclusion(engine)

    roster_reports = []
    for roster in rosters:
        view = _roster_view(roster)
        entries = view["entries"]
        stacks = roster_stack_flags(entries)
        anti = anticorrelation_flags(entries, exclusion)
        roster_reports.append(
            {
                "espn_team_id": roster.espn_team_id,
                "week": roster.week,
                "stacks": stacks,
                "anti_correlation": anti,
            }
        )

    data = {
        "espn_league_id": espn_league_id,
        "season": season,
        "week": week,
        "rosters": roster_reports,
    }
    return await _envelope(engine, espn_league_id, season, data)


@router.get("/league/{espn_league_id}/bye_outlook")
async def get_bye_outlook(
    espn_league_id: int,
    espn_team_id: Optional[int] = None,
    season: int = DRAFT_YEAR,
    week: Optional[int] = None,
    threshold: Optional[int] = None,
):
    """
    F2 bye planning: the draft-time league-wide cluster warning PLUS the
    in-season thin-week preview for the requested roster(s).

    - cluster: warn when `threshold` (default BYE_CLUSTER_THRESHOLD) or
      more of the league's likely starters share a bye week. Computed
      over every roster's starters in the league-week.
    - thin_week: per requested roster, the future week thinnest from
      byes. When espn_team_id is omitted, every roster is previewed.

    Degrades to status="no_schedule_data" when no ProGame rows exist.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    if week is None:
        week = league.latest_scoring_period
    if threshold is None:
        threshold = BYE_CLUSTER_THRESHOLD

    pro_games = await engine.find(
        ProGame, ProGame.season == season
    )

    conditions = [
        TeamWeekRoster.espn_league_id == espn_league_id,
        TeamWeekRoster.season == season,
        TeamWeekRoster.week == week,
    ]
    if espn_team_id is not None:
        conditions.append(TeamWeekRoster.espn_team_id == espn_team_id)
    criteria = {}
    for condition in conditions:
        criteria = condition if criteria == {} else criteria & condition
    rosters = await engine.find(TeamWeekRoster, criteria)
    rosters = sorted(rosters, key=lambda r: r.espn_team_id)

    roster_views = [_roster_view(r) for r in rosters]
    all_entries = [
        entry for view in roster_views for entry in view["entries"]
    ]

    cluster = bye_cluster_warning(all_entries, pro_games, threshold=threshold)

    thin_weeks = []
    for view in roster_views:
        preview = thin_week_preview(view["entries"], pro_games, week)
        thin_weeks.append(
            {
                "espn_team_id": view["espn_team_id"],
                "week": view["week"],
                "preview": preview,
            }
        )

    data = {
        "espn_league_id": espn_league_id,
        "season": season,
        "week": week,
        "threshold": threshold,
        "cluster": cluster,
        "thin_weeks": thin_weeks,
    }
    return await _envelope(engine, espn_league_id, season, data)
