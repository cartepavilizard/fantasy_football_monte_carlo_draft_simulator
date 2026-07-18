"use client";

import { useEffect, useRef, useState } from "react";
import { Spinner } from "@nextui-org/spinner";
import { FiBell, FiCheck, FiSmartphone, FiTrash2 } from "react-icons/fi";

import {
  useDeleteNotificationMutation,
  useGetNotificationsQuery,
  useMarkAllReadMutation,
  useMarkReadMutation,
} from "@/api/services/notifications";
import { EmptyStateHawk } from "@/components/mascots";
import { Notification } from "@/types";

function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);

  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;

  return `${Math.round(seconds / 86400)}d ago`;
}

// "first_lock_reminder" -> "First lock reminder" — readable without a
// hardcoded label map, since B5's producer set (C4/D2/E4/E8) is open-ended
function kindLabel(kind: string): string {
  const [first, ...rest] = kind.split("_");

  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(" ");
}

function NotificationRow({
  notification,
  onMarkRead,
  onDelete,
}: {
  notification: Notification;
  onMarkRead: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <li
      className={`flex flex-col gap-1 border-b border-default-100 p-3 ${
        notification.read ? "opacity-60" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          {!notification.read && (
            <span className="h-2 w-2 shrink-0 rounded-full bg-primary" />
          )}
          <span className="rounded-full bg-default-100 px-2 py-0.5 text-xs text-default-600">
            {kindLabel(notification.kind)}
          </span>
          {notification.pushed_at && (
            <span
              className="flex items-center gap-1 text-xs text-default-400"
              title={`Pushed to phone ${timeAgo(notification.pushed_at)}`}
            >
              <FiSmartphone />
            </span>
          )}
        </div>
        <span className="shrink-0 text-xs text-default-400">
          {timeAgo(notification.created_at)}
        </span>
      </div>
      <p className={`text-sm ${notification.read ? "" : "font-semibold"}`}>
        {notification.title}
      </p>
      <p className="text-sm text-default-500">{notification.body}</p>
      <div className="flex justify-end gap-3 pt-1">
        {!notification.read && (
          <button
            className="flex items-center gap-1 text-xs text-primary hover:underline"
            onClick={() => onMarkRead(notification.id)}
          >
            <FiCheck /> Mark read
          </button>
        )}
        <button
          className="flex items-center gap-1 text-xs text-danger hover:underline"
          onClick={() => onDelete(notification.id)}
        >
          <FiTrash2 /> Delete
        </button>
      </div>
    </li>
  );
}

export function NotificationsPanel() {
  const [open, setOpen] = useState(false);
  const [kindFilter, setKindFilter] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Always mounted (drives the bell's unread count) — light polling
  // keeps the badge current without needing a websocket.
  const { data, isLoading, refetch } = useGetNotificationsQuery(
    { limit: 50 },
    { pollingInterval: 60000 },
  );
  const [markRead] = useMarkReadMutation();
  const [markAllRead] = useMarkAllReadMutation();
  const [deleteNotification] = useDeleteNotificationMutation();

  const notifications = data?.notifications ?? [];
  const unreadCount = notifications.filter((n) => !n.read).length;
  const kinds = Array.from(new Set(notifications.map((n) => n.kind)));
  const visible = kindFilter
    ? notifications.filter((n) => n.kind === kindFilter)
    : notifications;

  useEffect(() => {
    if (!open) return;
    refetch();

    function onClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }

    document.addEventListener("mousedown", onClickOutside);

    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open, refetch]);

  return (
    <div ref={containerRef} className="relative">
      <button
        aria-label="Notifications"
        className="relative flex items-center text-default-500 hover:text-default-700"
        onClick={() => setOpen((value) => !value)}
      >
        <FiBell size={20} />
        {unreadCount > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[10px] font-bold text-white">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 flex max-h-[32rem] w-80 flex-col rounded-large border-medium border-default bg-background shadow-lg sm:w-96">
          <div className="flex items-center justify-between gap-2 border-b border-default-100 p-3">
            <h4 className="font-semibold">Notifications</h4>
            <button
              className="text-xs text-primary hover:underline disabled:text-default-300 disabled:no-underline"
              disabled={unreadCount === 0}
              onClick={() => markAllRead()}
            >
              Mark all read
            </button>
          </div>

          {kinds.length > 0 && (
            <div className="flex flex-wrap gap-1 border-b border-default-100 p-2">
              <button
                className={`rounded-full px-2 py-0.5 text-xs ${
                  kindFilter === null
                    ? "bg-primary text-white"
                    : "bg-default-100 text-default-600"
                }`}
                onClick={() => setKindFilter(null)}
              >
                All
              </button>
              {kinds.map((kind) => (
                <button
                  key={kind}
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    kindFilter === kind
                      ? "bg-primary text-white"
                      : "bg-default-100 text-default-600"
                  }`}
                  onClick={() => setKindFilter(kind)}
                >
                  {kindLabel(kind)}
                </button>
              ))}
            </div>
          )}

          <div className="overflow-y-auto">
            {isLoading ? (
              <div className="flex justify-center p-4">
                <Spinner size="sm" />
              </div>
            ) : visible.length === 0 ? (
              <div className="flex flex-col items-center gap-2 p-4 text-center">
                <EmptyStateHawk size={64} />
                <p className="text-sm text-default-500">Nothing here.</p>
              </div>
            ) : (
              <ul className="flex flex-col">
                {visible.map((notification) => (
                  <NotificationRow
                    key={notification.id}
                    notification={notification}
                    onDelete={(id) => deleteNotification({ id })}
                    onMarkRead={(id) => markRead({ id })}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
