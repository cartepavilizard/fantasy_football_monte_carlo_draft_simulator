# -*- coding: utf-8 -*-
"""
B3: the scheduled in-season sync loop. The on-demand /inseason/sync
endpoint (B1) already covers what one pass does; this only covers the
loop around it — lifecycle, the draft-day pause switch, failure
survival, and the gameday-vs-baseline cadence switch.
"""
import asyncio
import datetime

from models.config import DRAFT_YEAR
from scheduler import GAMEDAY_WEEKDAYS, InSeasonScheduler


# --- scheduler loop ---------------------------------------------------------------


class SyncRecorder:
    """Stands in for sync_all_leagues: same shape, no network"""

    def __init__(self, fail=False, leagues=None):
        self.calls = 0
        self.fail = fail
        self.leagues = leagues if leagues is not None else {111: {"week": 5}}

    async def __call__(self, engine, season):
        self.calls += 1
        if self.fail:
            raise RuntimeError("espn is down")
        return {"season": season, "leagues": dict(self.leagues)}


class ReminderRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, engine, espn_league_id, season, week):
        self.calls.append((espn_league_id, season, week))
        return []


class UsageRecorder:
    """Stands in for ingest_usage / ensure_usage_shift_notifications"""

    def __init__(self):
        self.calls = []

    async def __call__(self, engine, season, week):
        self.calls.append((season, week))
        return {}


class PracticeRecorder:
    """Stands in for ingest_practice_reports"""

    def __init__(self):
        self.calls = []

    async def __call__(self, engine, season, week):
        self.calls.append((season, week))
        return {}


def run_scheduler_for(scheduler, seconds):
    async def go():
        scheduler.start()
        await asyncio.sleep(seconds)
        await scheduler.stop()

    asyncio.run(go())


def make_scheduler(**kwargs):
    sync_fn = kwargs.pop("sync_fn", None) or SyncRecorder()
    reminder_fn = kwargs.pop("reminder_fn", None) or ReminderRecorder()
    usage_ingest_fn = kwargs.pop("usage_ingest_fn", None) or UsageRecorder()
    usage_notify_fn = kwargs.pop("usage_notify_fn", None) or UsageRecorder()
    practice_ingest_fn = kwargs.pop("practice_ingest_fn", None) or PracticeRecorder()
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("interval_hours", 0.03 / 3600)
    # both cadences tiny by default so lifecycle tests tick regardless of
    # which real-world weekday the suite happens to run on; cadence tests
    # below override both explicitly and inject now_fn to pin the weekday
    kwargs.setdefault("gameday_interval_hours", kwargs["interval_hours"])
    scheduler = InSeasonScheduler(
        lambda: None,
        sync_fn=sync_fn,
        reminder_fn=reminder_fn,
        usage_ingest_fn=usage_ingest_fn,
        usage_notify_fn=usage_notify_fn,
        practice_ingest_fn=practice_ingest_fn,
        **kwargs,
    )
    return (
        scheduler,
        sync_fn,
        reminder_fn,
        usage_ingest_fn,
        usage_notify_fn,
        practice_ingest_fn,
    )


def test_scheduler_ticks_on_interval_when_enabled():
    scheduler, sync_fn, reminder_fn, *_ = make_scheduler()
    run_scheduler_for(scheduler, 0.2)
    assert sync_fn.calls >= 3
    status = scheduler.status()
    assert status["last_run"] is not None
    assert status["last_error"] is None
    assert status["last_summary"]["leagues"] == {111: {"week": 5}}
    # every synced league with a known week gets a reminder pass
    assert reminder_fn.calls[-1] == (111, DRAFT_YEAR, 5)


def test_disabled_scheduler_never_syncs_but_keeps_running():
    scheduler, sync_fn, *_ = make_scheduler(enabled=False)
    run_scheduler_for(scheduler, 0.15)
    assert sync_fn.calls == 0


def test_draft_day_pause_stops_midstream():
    scheduler, sync_fn, *_ = make_scheduler()

    async def go():
        scheduler.start()
        await asyncio.sleep(0.1)
        scheduler.configure(enabled=False)  # the draft-day switch
        calls_when_paused = sync_fn.calls
        await asyncio.sleep(0.1)
        await scheduler.stop()
        return calls_when_paused

    calls_when_paused = asyncio.run(go())
    assert sync_fn.calls == calls_when_paused  # no runs after the pause


def test_failed_run_is_recorded_and_loop_survives():
    scheduler, sync_fn, reminder_fn, *_ = make_scheduler(sync_fn=SyncRecorder(fail=True))
    run_scheduler_for(scheduler, 0.2)
    assert sync_fn.calls >= 2  # kept ticking after the failure
    assert "espn is down" in scheduler.status()["last_error"]
    assert reminder_fn.calls == []  # never reached on a failing sync


def test_reminders_skip_leagues_with_no_known_week():
    leagues = {111: {"week": 5}, 222: {"week": None}}
    scheduler, sync_fn, reminder_fn, *_ = make_scheduler(
        sync_fn=SyncRecorder(leagues=leagues)
    )
    asyncio.run(scheduler.run_now())
    assert [call[0] for call in reminder_fn.calls] == [111]


def test_configure_rejects_bad_interval():
    scheduler, *_ = make_scheduler()
    try:
        scheduler.configure(interval_hours=0)
        assert False, "should have raised"
    except ValueError:
        pass


# --- gameday vs baseline cadence ---------------------------------------------------


def test_gameday_interval_used_wednesday_through_sunday():
    for weekday in GAMEDAY_WEEKDAYS:
        fake_now = datetime.datetime(2024, 9, 2 + weekday)  # a Monday + weekday
        scheduler, *_ = make_scheduler(
            interval_hours=24,
            gameday_interval_hours=6,
            now_fn=lambda d=fake_now: d,
        )
        assert scheduler.current_interval_hours() == 6


def test_baseline_interval_used_monday_and_tuesday():
    for weekday in (0, 1):
        fake_now = datetime.datetime(2024, 9, 2 + weekday)
        scheduler, *_ = make_scheduler(
            interval_hours=24,
            gameday_interval_hours=6,
            now_fn=lambda d=fake_now: d,
        )
        assert scheduler.current_interval_hours() == 24


# --- usage ingestion wiring (C4's cheap half, off by default) ----------------------


def test_usage_ingest_disabled_by_default_never_called():
    scheduler, _, _, usage_ingest_fn, usage_notify_fn, _ = make_scheduler(
        sync_fn=SyncRecorder(leagues={111: {"week": 5}})
    )
    asyncio.run(scheduler.run_now())
    assert usage_ingest_fn.calls == []
    assert usage_notify_fn.calls == []


def test_usage_ingest_runs_for_most_recent_completed_week_when_enabled():
    scheduler, _, _, usage_ingest_fn, usage_notify_fn, _ = make_scheduler(
        sync_fn=SyncRecorder(leagues={111: {"week": 5}}),
        usage_ingest_enabled=True,
    )
    asyncio.run(scheduler.run_now())
    # completed week = latest_scoring_period - 1 (usage trails the live week)
    assert usage_ingest_fn.calls == [(DRAFT_YEAR, 4)]
    assert usage_notify_fn.calls == [(DRAFT_YEAR, 4)]


def test_usage_ingest_uses_min_completed_week_across_leagues():
    leagues = {111: {"week": 5}, 222: {"week": 3}}
    scheduler, _, _, usage_ingest_fn, usage_notify_fn, _ = make_scheduler(
        sync_fn=SyncRecorder(leagues=leagues),
        usage_ingest_enabled=True,
    )
    asyncio.run(scheduler.run_now())
    # min(5-1, 3-1) = 2 — usage data always trails the least-current league
    assert usage_ingest_fn.calls == [(DRAFT_YEAR, 2)]


def test_usage_ingest_skipped_when_no_league_has_a_completed_week():
    leagues = {111: {"week": 1}, 222: {"week": None}}
    scheduler, _, _, usage_ingest_fn, usage_notify_fn, _ = make_scheduler(
        sync_fn=SyncRecorder(leagues=leagues),
        usage_ingest_enabled=True,
    )
    asyncio.run(scheduler.run_now())
    # week 1 has no prior completed week (1 - 1 = 0); nothing to ingest
    assert usage_ingest_fn.calls == []
    assert usage_notify_fn.calls == []


def test_usage_ingest_skipped_when_sync_never_learns_a_week():
    leagues = {111: {"week": None}}
    scheduler, _, _, usage_ingest_fn, usage_notify_fn, _ = make_scheduler(
        sync_fn=SyncRecorder(leagues=leagues),
        usage_ingest_enabled=True,
    )
    asyncio.run(scheduler.run_now())
    assert usage_ingest_fn.calls == []


# --- practice-report ingestion wiring (D2's cheap half, off by default) -----------


def test_practice_ingest_disabled_by_default_never_called():
    scheduler, _, _, _, _, practice_ingest_fn = make_scheduler(
        sync_fn=SyncRecorder(leagues={111: {"week": 5}})
    )
    asyncio.run(scheduler.run_now())
    assert practice_ingest_fn.calls == []


def test_practice_ingest_runs_for_the_live_week_when_enabled():
    scheduler, _, _, _, _, practice_ingest_fn = make_scheduler(
        sync_fn=SyncRecorder(leagues={111: {"week": 5}}),
        practice_ingest_enabled=True,
    )
    asyncio.run(scheduler.run_now())
    # unlike usage (which trails), practice reports are about the live week
    assert practice_ingest_fn.calls == [(DRAFT_YEAR, 5)]


def test_practice_ingest_covers_every_distinct_live_week_across_leagues():
    leagues = {111: {"week": 5}, 222: {"week": 3}}
    scheduler, _, _, _, _, practice_ingest_fn = make_scheduler(
        sync_fn=SyncRecorder(leagues=leagues),
        practice_ingest_enabled=True,
    )
    asyncio.run(scheduler.run_now())
    assert practice_ingest_fn.calls == [(DRAFT_YEAR, 3), (DRAFT_YEAR, 5)]


def test_practice_ingest_skipped_when_sync_never_learns_a_week():
    leagues = {111: {"week": None}}
    scheduler, _, _, _, _, practice_ingest_fn = make_scheduler(
        sync_fn=SyncRecorder(leagues=leagues),
        practice_ingest_enabled=True,
    )
    asyncio.run(scheduler.run_now())
    assert practice_ingest_fn.calls == []


def test_practice_ingest_never_reached_when_sync_fails():
    scheduler, _, _, _, _, practice_ingest_fn = make_scheduler(
        sync_fn=SyncRecorder(fail=True), practice_ingest_enabled=True
    )
    asyncio.run(scheduler.run_now())
    assert practice_ingest_fn.calls == []


# --- schedule endpoints -------------------------------------------------------------


def test_schedule_endpoints_control_the_running_scheduler(client):
    status = client.get("/inseason/schedule").json()
    assert status["running"] is True  # started with the app
    assert status["enabled"] is False  # env default: off outside compose

    updated = client.post(
        "/inseason/schedule?enabled=true&interval_hours=6"
    ).json()
    assert updated["enabled"] is True
    assert updated["interval_hours"] == 6.0
    assert updated["next_run"] is not None

    paused = client.post("/inseason/schedule?enabled=false").json()
    assert paused["enabled"] is False
    assert paused["next_run"] is None

    assert client.post("/inseason/schedule?interval_hours=0").status_code == 400
