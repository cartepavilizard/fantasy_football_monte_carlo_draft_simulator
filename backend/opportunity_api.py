# -*- coding: utf-8 -*-
"""
OPPORTUNITY SCANNER ENDPOINTS (PHASE E, TASK E4 — the report half)

The on-demand surface for everything the proactive scanner sees: a
read-only GET that re-runs the five trigger conditions against synced
Mongo state and returns the window/watch rows at any severity. This is
the release valve (spec §1) for marginal cases that don't clear the push
bar — only the hard triggers page the phone (models/opportunity_scanner.py
handles that, in the scheduler pass); the GET serves everything.

Like inseason_api / notifications_api, this module is cached-only by
construction: it imports nothing from data_sources, every read is Mongo,
and the response carries the freshness/warnings envelope so serving cached
injury data degrades visibly instead of looking fresh. Refreshing this
page never consumes push budget or mutates scan state — the report is a
pure re-evaluation (spec §5, §8).
"""
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException

from models.config import DRAFT_YEAR
from models.inseason import InSeasonLeague, league_freshness
from models.opportunity_scanner import trade_opportunity_report

router = APIRouter(prefix="/inseason", tags=["opportunities"])

_engine_getter: Optional[Callable] = None


def configure(engine_getter: Callable):
    """Late-bound engine getter, mirroring inseason_api.configure /
    notifications_api.configure so app.py's wiring pattern is identical
    and tests that swap app.engine are honored automatically."""
    global _engine_getter
    _engine_getter = engine_getter


def _engine():
    if _engine_getter is None:
        raise RuntimeError("opportunity_api.configure() was never called")
    return _engine_getter()


async def _envelope(engine, espn_league_id, season, data) -> dict:
    freshness = await league_freshness(engine, espn_league_id, season)
    return {
        "data": data,
        "freshness": freshness,
        "warnings": freshness["warnings"],
    }


@router.get("/league/{espn_league_id}/trade_opportunities")
async def get_trade_opportunities(
    espn_league_id: int,
    season: int = DRAFT_YEAR,
):
    """
    The on-demand opportunity report (E4): every current injury window the
    scanner sees, at `window` or `watch` severity, with the rival's weekly
    gap, your surplus pieces, and the E1 probe where one ran. Pure Mongo
    reads — no state writes, no notifications — so this is safe to refresh
    any number of times (the GET's purity is part of the spec contract).
    """
    engine = _engine()
    exists = await engine.find_one(
        InSeasonLeague,
        (InSeasonLeague.espn_league_id == espn_league_id)
        & (InSeasonLeague.season == season),
    )
    if exists is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"ESPN league {espn_league_id} has no synced data for "
                f"{season}; run POST /inseason/sync first"
            ),
        )
    data = await trade_opportunity_report(engine, espn_league_id, season)
    return await _envelope(engine, espn_league_id, season, data)
