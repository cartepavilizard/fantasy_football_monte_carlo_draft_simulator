# -*- coding: utf-8 -*-
"""
IN-APP NOTIFICATIONS BACKBONE (PHASE B, TASK B5)

The durable record of everything the app wants the user to know:
lock reminders now; usage-shift alerts (C4), injury downgrades (D2),
trade windows (E4), and deadline flags (E8) later — all created through
ensure_notification() so every producer inherits dedupe for free.

APP <-> CLAUDE ROUTINE CONTRACT
The app never pushes to the phone itself; a Claude Routine (scheduled
in the Claude app, runs in the cloud) polls the app and does the push.
Split of responsibilities:

- The APP owns detection: sync passes and B3's scheduled loop decide
  what is notification-worthy and insert rows here, deduped by
  dedupe_key, whether or not any Routine is listening. The panel shows
  them regardless — that's the durable record.
- The ROUTINE owns delivery, via exactly two endpoints
  (notifications_api.py):
    1. GET  /notifications/pending?channel=push
         -> undelivered notifications, oldest first
    2. POST /notifications/{id}/ack?channel=push
         -> marks one delivered (idempotent; re-acking is a no-op)
  The Routine's loop: poll -> push each notification's title/body to
  the Android Claude app -> ack it. Delivery is at-least-once by
  design: a crash between push and ack re-delivers on the next poll,
  and dedupe_key guarantees the app never creates the same logical
  event twice, so duplicates are bounded and rare.

Lock reminders (the two Routines Phase B ships) are detected by
ensure_lock_reminders() below from the synced NFL schedule: first lock
is the week's earliest kickoff — which is how the Wednesday season
opener is handled with no special case — and final lock is the latest.
A reminder is created once `now` enters the configured lead window
before the lock (config: FIRST_LOCK_REMINDER_HOURS /
FINAL_LOCK_REMINDER_HOURS) and never after the lock has passed.
"""
import datetime
from typing import List, Optional

from odmantic import Model
from odmantic import Field as ODField
from odmantic import query

from .config import FINAL_LOCK_REMINDER_HOURS, FIRST_LOCK_REMINDER_HOURS
from .inseason import ProGame, week_lock_times

# The only delivery channel Phase B defines. More channels would add a
# per-channel delivery record; don't widen until a second one exists.
PUSH_CHANNEL = "push"


class Notification(Model):
    """One durable in-app notification (and unit of Routine delivery)"""

    model_config = {"collection": "notifications"}

    kind: str  # first_lock_reminder | final_lock_reminder | usage_shift | ...
    dedupe_key: str  # one per logical event, e.g. "111:2026:w5:first_lock"
    title: str
    body: str
    espn_league_id: Optional[int] = None
    season: Optional[int] = None
    week: Optional[int] = None
    event_at: Optional[datetime.datetime] = None  # the moment it's about
    created_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)
    read: bool = False  # panel state
    pushed_at: Optional[datetime.datetime] = None  # set by the Routine's ack


async def ensure_notification(engine, **fields) -> Optional[Notification]:
    """
    Insert a notification unless its dedupe_key already exists; returns
    the new notification or None when deduped. Every producer must come
    through here — it is what makes detection safe to re-run.
    """
    existing = await engine.find_one(
        Notification, Notification.dedupe_key == fields["dedupe_key"]
    )
    if existing is not None:
        return None
    notification = Notification(**fields)
    await engine.save(notification)
    return notification


async def pending_notifications(engine, channel: str = PUSH_CHANNEL) -> list:
    """The Routine's poll: undelivered notifications, oldest first"""
    return await engine.find(
        Notification,
        Notification.pushed_at == None,  # noqa: E711
        sort=(query.asc(Notification.created_at), query.asc(Notification.id)),
    )


def _fmt(moment: datetime.datetime) -> str:
    """'Wednesday Sep 9, 7:20 PM' — weekday first so the Wednesday opener
    reads as the anomaly it is (built by hand: %-d/%-I aren't portable)"""
    time_part = moment.strftime("%I:%M %p").lstrip("0")
    return f"{moment.strftime('%A %b')} {moment.day}, {time_part}"


async def ensure_lock_reminders(
    engine,
    espn_league_id: int,
    season: int,
    week: int,
    now: Optional[datetime.datetime] = None,
    first_lead_hours: Optional[float] = None,
    final_lead_hours: Optional[float] = None,
) -> List[Notification]:
    """
    Create whichever lock reminders for one league-week are currently
    inside their lead window and not yet past. Idempotent (dedupe_key),
    so B3's loop and the on-demand sync can both call it every pass.
    """
    now = now or datetime.datetime.now()
    if first_lead_hours is None:
        first_lead_hours = FIRST_LOCK_REMINDER_HOURS
    if final_lead_hours is None:
        final_lead_hours = FINAL_LOCK_REMINDER_HOURS
    games = await engine.find(
        ProGame, (ProGame.season == season) & (ProGame.week == week)
    )
    locks = week_lock_times(games)
    if locks is None:
        return []

    created = []
    first_lock, final_lock = locks["first_lock"], locks["final_lock"]

    window_start = first_lock - datetime.timedelta(hours=first_lead_hours)
    if window_start <= now < first_lock:
        notification = await ensure_notification(
            engine,
            kind="first_lock_reminder",
            dedupe_key=f"{espn_league_id}:{season}:w{week}:first_lock",
            title=f"Week {week}: first lineup lock {_fmt(first_lock)}",
            body=(
                f"First lock of week {week} is {_fmt(first_lock)} "
                f"({locks['first_game']} kickoff). Players in that game "
                "lock then — set any early starters now."
            ),
            espn_league_id=espn_league_id,
            season=season,
            week=week,
            event_at=first_lock,
        )
        if notification:
            created.append(notification)

    window_start = final_lock - datetime.timedelta(hours=final_lead_hours)
    if final_lock != first_lock and window_start <= now < final_lock:
        notification = await ensure_notification(
            engine,
            kind="final_lock_reminder",
            dedupe_key=f"{espn_league_id}:{season}:w{week}:final_lock",
            title=f"Week {week}: final lineup lock {_fmt(final_lock)}",
            body=(
                f"Last chance for week {week} — the final game kicks off "
                f"{_fmt(final_lock)}. Check for injured or BYE players "
                "still in your lineup."
            ),
            espn_league_id=espn_league_id,
            season=season,
            week=week,
            event_at=final_lock,
        )
        if notification:
            created.append(notification)

    return created
