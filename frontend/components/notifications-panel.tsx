"use client";

import {
  useDeleteNotificationMutation,
  useGetNotificationsQuery,
  useMarkAllReadMutation,
  useMarkReadMutation,
} from "@/api/services/notifications";
import { NotificationsPanel as NotificationsPanelView } from "@hawkmode/ui/notifications-panel";

// Connected wrapper for the Hawk UI presentational NotificationsPanel: owns
// the live polling fetch and the mark-read/mark-all-read/delete mutations.
// The presentational half (open/close, kind filter, click-outside, layout)
// lives in frontend/packages/hawk-ui/src/notifications-panel.tsx.

export function NotificationsPanel() {
  // Always mounted (drives the bell's unread count) — light polling
  // keeps the badge current without needing a websocket.
  const { data, isLoading, refetch } = useGetNotificationsQuery(
    { limit: 50 },
    { pollingInterval: 60000 },
  );
  const [markRead] = useMarkReadMutation();
  const [markAllRead] = useMarkAllReadMutation();
  const [deleteNotification] = useDeleteNotificationMutation();

  return (
    <NotificationsPanelView
      notifications={data?.notifications ?? []}
      isLoading={isLoading}
      onMarkRead={(id) => markRead({ id })}
      onMarkAllRead={() => markAllRead()}
      onDelete={(id) => deleteNotification({ id })}
      onOpen={refetch}
    />
  );
}
