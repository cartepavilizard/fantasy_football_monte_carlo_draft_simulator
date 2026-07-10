# -*- coding: utf-8 -*-
"""
NOTIFICATIONS ENDPOINTS (PHASE B, TASK B5 — the contract half)

Two audiences:
- The in-app panel reads GET /notifications (newest first) and marks
  rows read. Richer panel CRUD (mark-all-read, delete, kind filters)
  is the speced cheap half — add it here.
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
async def list_notifications(unread_only: bool = False, limit: int = 50):
    """The panel's list: newest first, the durable record"""
    engine = _engine()
    criteria = Notification.read == False if unread_only else {}  # noqa: E712
    notifications = await engine.find(
        Notification,
        criteria,
        sort=(query.desc(Notification.created_at), query.desc(Notification.id)),
        limit=limit,
    )
    return {"notifications": [_dump(notification) for notification in notifications]}


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
