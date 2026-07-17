# -*- coding:utf-8 -*-  # noqa: E265
# -*- coding: utf-8 -*-
"""
HOARDING + BLOCKING ENDPOINTS (PHASE E, TASKS E6 + E5 — the read half)

Two GET endpoints, both strictly Mongo-only (no data_sources import, no
fetches), wrapped in B4's standard freshness/warnings envelope:

- GET /inseason/league/{espn_league_id}/hoarding — serves the STORED
  weekly report (never computes; the scan runs only via run_hoarding_scan
  or POST /inseason/sync's pipeline). The expensive rival×FA scan is off
  the read path by construction (spec §3).

- GET /inseason/league/{espn_league_id}/blocking — computes E5's report
  ON DEMAND (the join is cheap: a handful of Mongo reads + in-memory
  match). On-demand is fine here because, unlike E6's rival×FA product,
  this is one bounded join over rivals' rosters.

Engine binding mirrors notifications_api / inseason_api: configure() takes
a late-bound getter so tests that swap the engine are honored. This router
is NOT wired into app.py by this task (the orchestrator does that later);
tests build a FastAPI app that includes it. It lives in the cached-only
club — the purity test in test_hoarding.py enforces the data_sources ban
structurally, and the runtime test rigs the transport to explode.
"""
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException

from models.blocking import blocking_plays
from models.config import DRAFT_YEAR
from models.hoarding import HoardingReport
from models.inseason import InSeasonLeague, league_freshness

router = APIRouter(prefix="/inseason", tags=["hoarding"])

_engine_getter: Optional[Callable] = None


def configure(engine_getter: Callable):
    """Late-bound engine getter — matches notifications_api.configure."""
    global _engine_getter
    _engine_getter = engine_getter


def _engine():
    if _engine_getter is None:
        raise RuntimeError("hoarding_api.configure() was never called")
    return _engine_getter()


async def _league_or_404(engine, espn_league_id: int, season: int) -> InSeasonLeague:
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
    """The standard freshness + warnings envelope (B4's contract)."""
    freshness = await league_freshness(engine, espn_league_id, season)
    return {
        "data": data,
        "freshness": freshness,
        "warnings": freshness["warnings"],
    }


@router.get("/league/{espn_league_id}/hoarding")
async def get_hoarding(
    espn_league_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    The stored weekly post-waivers hoarding report (E6). Serves whatever
    report exists for the league-week with its generated_at; NEVER
    computes (the scan is expensive and runs via the scheduler). Returns
    data=None when no report has been generated yet. Mongo-only, inherits
    B4's cached-only constraint.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    report = await engine.find_one(
        HoardingReport,
        (HoardingReport.espn_league_id == espn_league_id)
        & (HoardingReport.season == season)
        & (HoardingReport.week == week),
    )
    data = report.model_dump(exclude={"id"}) if report else None
    return await _envelope(engine, espn_league_id, season, data)


@router.get("/league/{espn_league_id}/blocking")
async def get_blocking(
    espn_league_id: int,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
):
    """
    E5 blocking plays for one league-week, computed on demand: rivals'
    injured-star handcuffs worth grabbing purely to deny. The join is
    cheap (bounded rivals' rosters + C7 map + D2 designations + the FA
    pool), so unlike E6 it computes on the read path. Mongo-only, inherits
    B4's cached-only constraint.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    week = week or league.latest_scoring_period
    data = await blocking_plays(engine, espn_league_id, season, week)
    return await _envelope(engine, espn_league_id, season, data)
