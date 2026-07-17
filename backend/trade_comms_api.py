# -*- coding: utf-8 -*-
"""
TRADE-COMMS ENDPOINTS (PHASE E, TASKS E7 + E8)

Two GET endpoints over E1's cached data, patterned on notifications_api.py
(`configure(engine_getter)` + a late-bound `_engine()` getter) and
inseason_api's `/inseason` prefix:

- GET /inseason/league/{espn_league_id}/trade/message
    Message preview for a proposal (E7): builds E1's context, evaluates the
    proposal, and renders the friendly message via `render_trade_message`.
    Pure Mongo reads — accepts the proposal as query params (comma-separated
    player ids) so it stays a GET, mirroring how player_values is a GET.

- GET /inseason/league/{espn_league_id}/deadline_report
    E8's per-league deadline report: per-team buy/sell window flags from
    `InSeasonLeague.trade_deadline` + wins/losses, with E1's `playoff_value`
    attached where buildable. League-scoped, so it carries the standard
    freshness + warnings envelope.

Like every other in-season read, this module is cached-only by construction:
it imports nothing from `data_sources`, direct or transitive. The proposal
body for the message endpoint is a read request (no fetch trigger), so a GET
is appropriate — the same reasoning E1's player_values uses.

NOTE: this router is defined here but not yet mounted in app.py (that wiring
lands when the orchestrator scheduler is hooked up). The route functions are
testable directly and via a mounted test app.
"""
from typing import Callable, List, Optional

from fastapi import APIRouter, HTTPException

from models.config import DRAFT_YEAR
from models.deadline_awareness import deadline_report
from models.trade_messaging import render_trade_message
from models.trade_valuation import build_context, evaluate_trade, validate_trade
from models.inseason import InSeasonLeague, league_freshness

router = APIRouter(prefix="/inseason", tags=["trade_comms"])

_engine_getter: Optional[Callable] = None


def configure(engine_getter: Callable):
    """Late-bound engine getter, same pattern as notifications_api /
    inseason_api: tests that swap app.engine are honored automatically."""
    global _engine_getter
    _engine_getter = engine_getter


def _engine():
    if _engine_getter is None:
        raise RuntimeError("trade_comms_api.configure() was never called")
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
    freshness = await league_freshness(engine, espn_league_id, season)
    return {
        "data": data,
        "freshness": freshness,
        "warnings": freshness["warnings"],
    }


def _parse_ids(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    ids: List[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            ids.append(int(token))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"player ids must be integers; got '{token}'",
            )
    return ids


@router.get("/league/{espn_league_id}/trade/message")
async def get_trade_message(
    espn_league_id: int,
    team_a: int,
    team_b: int,
    sends_a: str,
    sends_b: str,
    week: Optional[int] = None,
    season: int = DRAFT_YEAR,
    willingness: Optional[str] = None,
):
    """
    E7: render a friendly, non-salesy message framing a trade proposal,
    quoting real projection and matchup numbers from E1's evaluation. Pure
    Mongo reads (build_context -> evaluate_trade -> render_trade_message);
    accepts the proposal as query params so it stays a GET, like
    player_values. The optional `willingness` query param selects a tone
    variant only — its text never appears in the message.
    """
    engine = _engine()
    league = await _league_or_404(engine, espn_league_id, season)
    sends_a_ids = _parse_ids(sends_a)
    sends_b_ids = _parse_ids(sends_b)
    ctx = await build_context(engine, league, week=week)
    errors = validate_trade(ctx, team_a, team_b, sends_a_ids, sends_b_ids)
    if errors:
        raise HTTPException(status_code=422, detail=errors[0])
    evaluation = evaluate_trade(ctx, team_a, team_b, sends_a_ids, sends_b_ids)
    message = render_trade_message(evaluation, willingness_label=willingness)
    data = {"message": message, "evaluation": evaluation}
    return await _envelope(engine, espn_league_id, season, data)


@router.get("/league/{espn_league_id}/deadline_report")
async def get_deadline_report(
    espn_league_id: int,
    season: int = DRAFT_YEAR,
):
    """
    E8: per-league trade-deadline report — buy/sell window flags per team
    from `InSeasonLeague.trade_deadline` + wins/losses, with E1's
    `playoff_value` attached where buildable. A league with no
    `trade_deadline` returns `in_window=False` and no team flags. Mongo-only,
    inherits B4's cached-only constraint; league-scoped, so it carries the
    freshness + warnings envelope.
    """
    engine = _engine()
    await _league_or_404(engine, espn_league_id, season)
    data = await deadline_report(engine, espn_league_id, season)
    return await _envelope(engine, espn_league_id, season, data)
