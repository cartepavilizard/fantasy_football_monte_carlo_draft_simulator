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
    RANKINGS_REFRESH_ENABLED,
    RANKINGS_REFRESH_INTERVAL_HOURS,
    SCORING_FORMAT,
)

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
