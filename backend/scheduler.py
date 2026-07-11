# -*- coding: utf-8 -*-
"""
SCHEDULED RANKINGS REFRESH (Phase 5)

A single in-process asyncio loop that re-runs the full source refresh on
an interval — daily is right for the off-season; tighten the interval
via env or the /rankings/schedule endpoint as draft season approaches.

Draft-day switch: POST /rankings/schedule?enabled=false pauses the loop
at runtime, so no scheduled job ever races a live draft; on-demand
refresh (POST /rankings/refresh) always remains available. The loop
sleeps first (no refresh burst on every container restart) and one
failed run never kills the schedule — the error is kept for status.
"""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from models.config import (
    DRAFT_YEAR,
    INSEASON_SYNC_ENABLED,
    INSEASON_SYNC_GAMEDAY_INTERVAL_HOURS,
    INSEASON_SYNC_INTERVAL_HOURS,
    RANKINGS_REFRESH_ENABLED,
    RANKINGS_REFRESH_INTERVAL_HOURS,
    SCORING_FORMAT,
)

# Wed-Sun: the days a synced week actually has games in flight
GAMEDAY_WEEKDAYS = (2, 3, 4, 5, 6)

MIN_SLEEP_SECONDS = 0.05  # floor so a misconfigured interval can't busy-loop


class RankingsScheduler:
    def __init__(
        self,
        engine_getter,
        refresh_fn=None,
        enabled: Optional[bool] = None,
        interval_hours: Optional[float] = None,
    ):
        if refresh_fn is None:
            from data_sources.service import refresh_rankings

            refresh_fn = refresh_rankings
        self._engine_getter = engine_getter  # late-bound: tests swap engines
        self._refresh_fn = refresh_fn
        self.enabled = (
            RANKINGS_REFRESH_ENABLED if enabled is None else enabled
        )
        self.interval_hours = (
            RANKINGS_REFRESH_INTERVAL_HOURS
            if interval_hours is None
            else interval_hours
        )
        self._task: Optional[asyncio.Task] = None
        self._woke_at: Optional[datetime] = None
        self.last_run: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.last_summary: Optional[dict] = None

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        if self._task is None or self._task.done():
            self._woke_at = datetime.now()
            self._task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while True:
            await asyncio.sleep(
                max(MIN_SLEEP_SECONDS, self.interval_hours * 3600)
            )
            self._woke_at = datetime.now()
            if not self.enabled:
                continue
            await self.run_now()

    async def run_now(self) -> dict:
        """One refresh pass; failures are recorded, never raised"""
        self.last_run = datetime.now()
        try:
            self.last_summary = await self._refresh_fn(
                self._engine_getter(), DRAFT_YEAR, SCORING_FORMAT
            )
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_summary = None
        return self.status()

    # -- control surface ----------------------------------------------------

    def configure(
        self,
        enabled: Optional[bool] = None,
        interval_hours: Optional[float] = None,
    ):
        if enabled is not None:
            self.enabled = enabled
        if interval_hours is not None:
            if interval_hours <= 0:
                raise ValueError("interval_hours must be positive")
            self.interval_hours = interval_hours

    def status(self) -> dict:
        next_run = None
        if self._task is not None and not self._task.done() and self.enabled:
            reference = self._woke_at or datetime.now()
            next_run = (
                reference + timedelta(hours=self.interval_hours)
            ).isoformat()
        return {
            "enabled": self.enabled,
            "running": self._task is not None and not self._task.done(),
            "interval_hours": self.interval_hours,
            "next_run": next_run,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_error": self.last_error,
            "last_summary": self.last_summary,
        }


class InSeasonScheduler:
    """
    In-season ESPN sync loop (Phase B, task B3): mirrors RankingsScheduler's
    lifecycle and draft-day-safe pause switch exactly, but the interval
    tightens Wed-Sun (a synced week actually has games in flight) and
    relaxes the rest of the week — re-evaluated every wake-up rather than
    fixed at start, so a day boundary crossed mid-sleep is never missed.
    Each pass also refreshes any due lock reminders for every league
    synced with a known current week.
    """

    def __init__(
        self,
        engine_getter,
        sync_fn=None,
        reminder_fn=None,
        enabled: Optional[bool] = None,
        interval_hours: Optional[float] = None,
        gameday_interval_hours: Optional[float] = None,
        now_fn=None,
    ):
        if sync_fn is None:
            from data_sources.espn_league import sync_all_leagues

            sync_fn = sync_all_leagues
        if reminder_fn is None:
            from models.notifications import ensure_lock_reminders

            reminder_fn = ensure_lock_reminders
        self._engine_getter = engine_getter  # late-bound: tests swap engines
        self._sync_fn = sync_fn
        self._reminder_fn = reminder_fn
        self._now_fn = now_fn or datetime.now  # swappable: tests fake weekday
        self.enabled = (
            INSEASON_SYNC_ENABLED if enabled is None else enabled
        )
        self.interval_hours = (
            INSEASON_SYNC_INTERVAL_HOURS if interval_hours is None else interval_hours
        )
        self.gameday_interval_hours = (
            INSEASON_SYNC_GAMEDAY_INTERVAL_HOURS
            if gameday_interval_hours is None
            else gameday_interval_hours
        )
        self._task: Optional[asyncio.Task] = None
        self._woke_at: Optional[datetime] = None
        self.last_run: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.last_summary: Optional[dict] = None

    # -- cadence --------------------------------------------------------------

    def current_interval_hours(self) -> float:
        if self._now_fn().weekday() in GAMEDAY_WEEKDAYS:
            return self.gameday_interval_hours
        return self.interval_hours

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        if self._task is None or self._task.done():
            self._woke_at = datetime.now()
            self._task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        while True:
            await asyncio.sleep(
                max(MIN_SLEEP_SECONDS, self.current_interval_hours() * 3600)
            )
            self._woke_at = datetime.now()
            if not self.enabled:
                continue
            await self.run_now()

    async def run_now(self) -> dict:
        """One sync pass plus due lock reminders; failures are recorded,
        never raised"""
        self.last_run = datetime.now()
        try:
            engine = self._engine_getter()
            summary = await self._sync_fn(engine, DRAFT_YEAR)
            for espn_league_id, league_summary in summary["leagues"].items():
                week = league_summary["week"]
                if week is not None:
                    await self._reminder_fn(engine, espn_league_id, DRAFT_YEAR, week)
            self.last_summary = summary
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_summary = None
        return self.status()

    # -- control surface ----------------------------------------------------

    def configure(
        self,
        enabled: Optional[bool] = None,
        interval_hours: Optional[float] = None,
    ):
        if enabled is not None:
            self.enabled = enabled
        if interval_hours is not None:
            if interval_hours <= 0:
                raise ValueError("interval_hours must be positive")
            self.interval_hours = interval_hours

    def status(self) -> dict:
        next_run = None
        if self._task is not None and not self._task.done() and self.enabled:
            reference = self._woke_at or datetime.now()
            next_run = (
                reference + timedelta(hours=self.current_interval_hours())
            ).isoformat()
        return {
            "enabled": self.enabled,
            "running": self._task is not None and not self._task.done(),
            "interval_hours": self.interval_hours,
            "gameday_interval_hours": self.gameday_interval_hours,
            "next_run": next_run,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "last_error": self.last_error,
            "last_summary": self.last_summary,
        }
