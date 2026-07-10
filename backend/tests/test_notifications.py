# -*- coding: utf-8 -*-
"""
B5: the notifications backbone. Covers the dedupe guarantee every
producer inherits, the lock-reminder detection core (Wednesday opener
included), and the app<->Routine delivery contract: poll pending ->
push -> ack, at-least-once, idempotent.
"""
import asyncio
import datetime

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.config import DRAFT_YEAR
from models.inseason import ProGame
from models.notifications import (
    Notification,
    ensure_lock_reminders,
    ensure_notification,
)

SEASON, LEAGUE_ID, WEEK = DRAFT_YEAR, 111, 1

# Week 1 with the Wednesday season opener: Wed 7:20 PM, Sun 1 PM, Mon 8:15 PM
# (dates computed from SEASON so the suite doesn't care what year config picks)
_first_wednesday = datetime.date(SEASON, 9, 1)
while _first_wednesday.weekday() != 2:
    _first_wednesday += datetime.timedelta(days=1)
WEDNESDAY_KICKOFF = datetime.datetime.combine(
    _first_wednesday, datetime.time(19, 20)
)
SUNDAY_KICKOFF = WEDNESDAY_KICKOFF + datetime.timedelta(days=4, hours=-6, minutes=-20)
MONDAY_KICKOFF = WEDNESDAY_KICKOFF + datetime.timedelta(days=5, minutes=55)
assert WEDNESDAY_KICKOFF.strftime("%A") == "Wednesday"


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-notifications")


def seed_week(engine, kickoffs=None):
    games = kickoffs or [
        ("SEA", "KC", WEDNESDAY_KICKOFF),
        ("BUF", "ARI", SUNDAY_KICKOFF),
        ("DET", "GB", MONDAY_KICKOFF),
    ]

    async def go():
        for index, (home, away, kickoff) in enumerate(games):
            await engine.save(
                ProGame(
                    season=SEASON,
                    week=WEEK,
                    espn_game_id=9000 + index,
                    home_team=home,
                    away_team=away,
                    kickoff=kickoff,
                )
            )

    asyncio.run(go())


def reminders_at(engine, now, **kwargs):
    return asyncio.run(
        ensure_lock_reminders(engine, LEAGUE_ID, SEASON, WEEK, now=now, **kwargs)
    )


def all_notifications(engine):
    async def go():
        return await engine.find(Notification)

    return asyncio.run(go())


# --- dedupe: the guarantee every producer inherits -------------------------------


def test_ensure_notification_dedupes_on_key():
    engine = make_engine()
    fields = dict(
        kind="usage_shift", dedupe_key="usage:jsn:w5", title="t", body="b"
    )
    first = asyncio.run(ensure_notification(engine, **fields))
    second = asyncio.run(ensure_notification(engine, **fields))
    assert first is not None and second is None
    assert len(all_notifications(engine)) == 1


# --- lock reminder detection ------------------------------------------------------


def test_first_lock_reminder_calls_out_the_wednesday_opener():
    engine = make_engine()
    seed_week(engine)
    # Wednesday morning: inside the 24h first-lock window, far from final
    created = reminders_at(engine, WEDNESDAY_KICKOFF - datetime.timedelta(hours=10))
    assert [notification.kind for notification in created] == ["first_lock_reminder"]
    reminder = created[0]
    assert "Wednesday" in reminder.body  # the opener reads as the anomaly it is
    assert "KC @ SEA" in reminder.body
    assert reminder.event_at == WEDNESDAY_KICKOFF
    assert reminder.week == WEEK


def test_final_lock_reminder_inside_its_own_window():
    engine = make_engine()
    seed_week(engine)
    created = reminders_at(engine, MONDAY_KICKOFF - datetime.timedelta(hours=2))
    assert [notification.kind for notification in created] == ["final_lock_reminder"]
    assert "BYE" in created[0].body  # the injured/BYE-starter nudge


def test_no_reminders_outside_windows_or_after_lock():
    engine = make_engine()
    seed_week(engine)
    # three days early: nothing
    assert reminders_at(engine, WEDNESDAY_KICKOFF - datetime.timedelta(days=3)) == []
    # after the final game kicked off: nothing (the moment has passed)
    assert reminders_at(engine, MONDAY_KICKOFF + datetime.timedelta(hours=1)) == []
    # no schedule synced at all: nothing to reason about, no crash
    assert reminders_at(make_engine(), WEDNESDAY_KICKOFF) == []


def test_reminders_are_idempotent_across_repeated_scheduler_passes():
    engine = make_engine()
    seed_week(engine)
    now = WEDNESDAY_KICKOFF - datetime.timedelta(hours=10)
    assert len(reminders_at(engine, now)) == 1
    assert reminders_at(engine, now) == []  # B3's loop can call this every pass
    assert len(all_notifications(engine)) == 1


def test_single_game_week_skips_the_redundant_final_reminder():
    engine = make_engine()
    seed_week(engine, kickoffs=[("SEA", "KC", WEDNESDAY_KICKOFF)])
    created = reminders_at(
        engine,
        WEDNESDAY_KICKOFF - datetime.timedelta(hours=1),
        first_lead_hours=24,
        final_lead_hours=3,
    )
    assert [notification.kind for notification in created] == ["first_lock_reminder"]


# --- the Routine delivery contract ------------------------------------------------


def seed_notifications(app_module, count=2):
    engine = app_module.engine

    async def go():
        created = []
        for index in range(count):
            notification = await ensure_notification(
                engine,
                kind="first_lock_reminder",
                dedupe_key=f"{LEAGUE_ID}:{SEASON}:w{index}:first_lock",
                title=f"Week {index}: first lineup lock",
                body="Set your early starters now.",
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=index,
            )
            created.append(notification)
        return created

    return asyncio.run(go())


def test_routine_poll_push_ack_flow(client, app_module):
    first, second = seed_notifications(app_module)

    # 1. the Routine polls: oldest first, everything undelivered
    pending = client.get("/notifications/pending?channel=push").json()["pending"]
    assert [entry["id"] for entry in pending] == [str(first.id), str(second.id)]

    # 2. it pushes the first one to the phone, then acks it
    acked = client.post(f"/notifications/{first.id}/ack?channel=push").json()
    assert acked["pushed_at"] is not None

    # 3. next poll only returns what is still undelivered
    pending = client.get("/notifications/pending").json()["pending"]
    assert [entry["id"] for entry in pending] == [str(second.id)]

    # 4. at-least-once safety: a re-ack (crash between push and ack,
    #    then retry) is a no-op, not an error
    re_acked = client.post(f"/notifications/{first.id}/ack").json()
    assert re_acked["pushed_at"] == acked["pushed_at"]


def test_unknown_channel_is_rejected(client, app_module):
    seed_notifications(app_module, count=1)
    assert client.get("/notifications/pending?channel=email").status_code == 400
    notification_id = client.get("/notifications/pending").json()["pending"][0]["id"]
    assert (
        client.post(f"/notifications/{notification_id}/ack?channel=sms").status_code
        == 400
    )


def test_panel_lists_newest_first_and_marks_read(client, app_module):
    first, second = seed_notifications(app_module)
    listed = client.get("/notifications").json()["notifications"]
    assert [entry["id"] for entry in listed] == [str(second.id), str(first.id)]
    assert all(entry["read"] is False for entry in listed)

    marked = client.post(f"/notifications/{first.id}/read").json()
    assert marked["read"] is True
    unread = client.get("/notifications?unread_only=true").json()["notifications"]
    assert [entry["id"] for entry in unread] == [str(second.id)]
    # reading is panel state; delivery tracking is untouched
    assert marked["pushed_at"] is None


def test_ack_missing_notification_404s(client, app_module):
    assert (
        client.post("/notifications/64b0f0f0f0f0f0f0f0f0f0f0/ack").status_code == 404
    )
