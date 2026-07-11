# -*- coding: utf-8 -*-
"""
C1: the Thursday-morning pull. Wall-clock cadence math, one review
notification per synced league, failure survival, and the runtime
control endpoints — the sync itself is B1's, injected here.
"""
import asyncio
import datetime

from models.config import DRAFT_YEAR
from scheduler import LineupPullScheduler


class SyncRecorder:
    def __init__(self, fail=False, leagues=None):
        self.calls = 0
        self.fail = fail
        self.leagues = leagues if leagues is not None else {111: {"week": 5}}

    async def __call__(self, engine, season):
        self.calls += 1
        if self.fail:
            raise RuntimeError("espn is down")
        return {"season": season, "leagues": dict(self.leagues)}


class ReviewRecorder:
    class _Notification:
        title = "Week 5 lineup review: The Family League"

    def __init__(self):
        self.calls = []

    async def __call__(self, engine, espn_league_id, season, week):
        self.calls.append((espn_league_id, season, week))
        return self._Notification()


def make_scheduler(**kwargs):
    sync_fn = kwargs.pop("sync_fn", None) or SyncRecorder()
    review_fn = kwargs.pop("review_fn", None) or ReviewRecorder()
    kwargs.setdefault("enabled", True)
    scheduler = LineupPullScheduler(
        lambda: None, sync_fn=sync_fn, review_fn=review_fn, **kwargs
    )
    return scheduler, sync_fn, review_fn


# --- cadence math -----------------------------------------------------------------


def test_seconds_until_next_pull_targets_thursday_morning():
    wednesday_noon = datetime.datetime(2026, 9, 9, 12, 0)  # a Wednesday
    scheduler, _, _ = make_scheduler(
        weekday=3, hour=7, now_fn=lambda: wednesday_noon
    )
    assert scheduler.seconds_until_next_pull() == 19 * 3600


def test_same_day_before_the_hour_pulls_today():
    thursday_6am = datetime.datetime(2026, 9, 10, 6, 0)
    scheduler, _, _ = make_scheduler(weekday=3, hour=7, now_fn=lambda: thursday_6am)
    assert scheduler.seconds_until_next_pull() == 3600


def test_same_day_after_the_hour_waits_a_week():
    thursday_8am = datetime.datetime(2026, 9, 10, 8, 0)
    scheduler, _, _ = make_scheduler(weekday=3, hour=7, now_fn=lambda: thursday_8am)
    assert scheduler.seconds_until_next_pull() == (7 * 24 - 1) * 3600


# --- run behavior -----------------------------------------------------------------


def test_run_now_syncs_then_reviews_each_league_with_a_week():
    leagues = {111: {"week": 5}, 222: {"week": None}, 333: {"week": 5}}
    scheduler, sync_fn, review_fn = make_scheduler(
        sync_fn=SyncRecorder(leagues=leagues)
    )
    status = asyncio.run(scheduler.run_now())
    assert sync_fn.calls == 1
    assert [call[0] for call in review_fn.calls] == [111, 333]
    assert review_fn.calls[0] == (111, DRAFT_YEAR, 5)
    assert len(status["last_summary"]["reviews"]) == 2
    assert status["last_error"] is None


def test_failed_pull_is_recorded_never_raised():
    scheduler, _, review_fn = make_scheduler(sync_fn=SyncRecorder(fail=True))
    status = asyncio.run(scheduler.run_now())
    assert "espn is down" in status["last_error"]
    assert review_fn.calls == []


def test_loop_ticks_and_pause_switch_works(monkeypatch):
    scheduler, sync_fn, _ = make_scheduler()
    monkeypatch.setattr(scheduler, "seconds_until_next_pull", lambda: 0.03)

    async def go():
        scheduler.start()
        await asyncio.sleep(0.15)
        scheduler.configure(enabled=False)
        calls_when_paused = sync_fn.calls
        await asyncio.sleep(0.1)
        await scheduler.stop()
        return calls_when_paused

    calls_when_paused = asyncio.run(go())
    assert calls_when_paused >= 2
    assert sync_fn.calls == calls_when_paused


def test_configure_validates_weekday_and_hour():
    scheduler, _, _ = make_scheduler()
    scheduler.configure(weekday=0, hour=23)
    assert (scheduler.weekday, scheduler.hour) == (0, 23)
    for bad in ({"weekday": 7}, {"hour": 24}, {"weekday": -1}):
        try:
            scheduler.configure(**bad)
            assert False, f"should have rejected {bad}"
        except ValueError:
            pass


# --- runtime control endpoints -----------------------------------------------------


def test_lineup_schedule_endpoints(client):
    status = client.get("/inseason/lineup_schedule").json()
    assert status["running"] is True
    assert status["enabled"] is False  # env default: off
    assert (status["weekday"], status["hour"]) == (3, 7)

    updated = client.post(
        "/inseason/lineup_schedule?enabled=true&weekday=4&hour=6"
    ).json()
    assert updated["enabled"] is True
    assert (updated["weekday"], updated["hour"]) == (4, 6)
    assert updated["next_run"] is not None

    assert client.post("/inseason/lineup_schedule?hour=24").status_code == 400
