# -*- coding: utf-8 -*-
"""
NOTIFICATIONS ENDPOINTS (PHASE B, TASK B5 — the contract half)

Two audiences:
- The in-app panel reads GET /notifications (newest first, optional
  `unread_only` / `kind` filters), marks one row read (POST
  /{id}/read), marks everything read at once (POST /read_all), or
  deletes a row (DELETE /{id}). All panel state — read/deleted — is
  independent of the ack/pending contract below.
- The Claude Routine that pushes to the Android app speaks exactly two
  endpoints: GET /notifications/pending?channel=push and
  POST /notifications/{id}/ack?channel=push. See models/notifications.py
  for the full app<->Routine contract (at-least-once delivery, dedupe).

Like inseason_api, this module is cached-only by construction: it
imports nothing from data_sources, and the purity test covers it too.
"""
import datetime
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException
from odmantic import ObjectId, query

from models.notifications import (
    PUSH_CHANNEL,
    Notification,
    pending_notifications,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

_engine_getter: Optional[Callable] = None


def configure(engine_getter: Callable):
    global _engine_getter
    _engine_getter = engine_getter


def _engine():
    if _engine_getter is None:
        raise RuntimeError("notifications_api.configure() was never called")
    return _engine_getter()


def _check_channel(channel: str):
    if channel != PUSH_CHANNEL:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown channel '{channel}'; only '{PUSH_CHANNEL}' exists",
        )


def _dump(notification: Notification) -> dict:
    data = notification.model_dump(exclude={"id"})
    data["id"] = str(notification.id)
    return data


async def _notification_or_404(engine, notification_id: ObjectId) -> Notification:
    notification = await engine.find_one(
        Notification, Notification.id == notification_id
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="No such notification")
    return notification


@router.get("")
async def list_notifications(
    unread_only: bool = False, kind: Optional[str] = None, limit: int = 50
):
    """The panel's list: newest first, the durable record"""
    engine = _engine()
    conditions = []
    if unread_only:
        conditions.append(Notification.read == False)  # noqa: E712
    if kind is not None:
        conditions.append(Notification.kind == kind)
    criteria = {}
    for condition in conditions:
        criteria = condition if criteria == {} else criteria & condition
    notifications = await engine.find(
        Notification,
        criteria,
        sort=(query.desc(Notification.created_at), query.desc(Notification.id)),
        limit=limit,
    )
    return {"notifications": [_dump(notification) for notification in notifications]}


@router.post("/read_all")
async def mark_all_read():
    """Panel bulk action: every currently-unread notification becomes read"""
    engine = _engine()
    unread = await engine.find(Notification, Notification.read == False)  # noqa: E712
    for notification in unread:
        notification.read = True
    if unread:
        await engine.save_all(unread)
    return {"updated": len(unread)}


@router.get("/pending")
async def get_pending(channel: str = PUSH_CHANNEL):
    """The Routine's poll: undelivered notifications, oldest first"""
    _check_channel(channel)
    engine = _engine()
    notifications = await pending_notifications(engine, channel)
    return {
        "channel": channel,
        "pending": [_dump(notification) for notification in notifications],
    }


@router.post("/{notification_id}/ack")
async def ack_notification(notification_id: ObjectId, channel: str = PUSH_CHANNEL):
    """
    The Routine's delivery confirmation. Idempotent: re-acking returns
    the notification unchanged, so at-least-once delivery is safe.
    """
    _check_channel(channel)
    engine = _engine()
    notification = await _notification_or_404(engine, notification_id)
    if notification.pushed_at is None:
        notification.pushed_at = datetime.datetime.now()
        await engine.save(notification)
    return _dump(notification)


@router.post("/{notification_id}/read")
async def mark_read(notification_id: ObjectId):
    """Panel state only; delivery tracking is ack's job"""
    engine = _engine()
    notification = await _notification_or_404(engine, notification_id)
    if not notification.read:
        notification.read = True
        await engine.save(notification)
    return _dump(notification)


@router.delete("/{notification_id}")
async def delete_notification(notification_id: ObjectId):
    """Removes the durable record entirely; ack/pending state plays no
    part in whether a notification can be deleted"""
    engine = _engine()
    notification = await _notification_or_404(engine, notification_id)
    await engine.delete(notification)
    return {"deleted": True}
