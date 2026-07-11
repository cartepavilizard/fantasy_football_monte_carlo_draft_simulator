import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import { Notification } from "@/types";

// Url for all notification-panel operations. The Routine's own
// poll/ack endpoints (/notifications/pending, /notifications/{id}/ack)
// are not used by the app — this file is the panel's half only.
const notificationsUrl = "/notifications";

export const notificationsApi = createApi({
  reducerPath: "notificationsApi",
  baseQuery: fetchBaseQuery(baseQuery),
  tagTypes: ["Notification"],
  endpoints: (builder) => ({
    getNotifications: builder.query<
      { notifications: Notification[] },
      { unreadOnly?: boolean; kind?: string; limit?: number } | void
    >({
      query: (args) => ({
        url: notificationsUrl,
        params: {
          ...(args?.unreadOnly && { unread_only: true }),
          ...(args?.kind != null && { kind: args.kind }),
          ...(args?.limit != null && { limit: args.limit }),
        },
      }),
      providesTags: ["Notification"],
    }),

    markRead: builder.mutation<Notification, { id: string }>({
      query: ({ id }) => ({
        url: `${notificationsUrl}/${id}/read`,
        method: "POST",
      }),
      invalidatesTags: ["Notification"],
    }),

    markAllRead: builder.mutation<{ updated: number }, void>({
      query: () => ({
        url: `${notificationsUrl}/read_all`,
        method: "POST",
      }),
      invalidatesTags: ["Notification"],
    }),

    deleteNotification: builder.mutation<{ deleted: boolean }, { id: string }>({
      query: ({ id }) => ({
        url: `${notificationsUrl}/${id}`,
        method: "DELETE",
      }),
      invalidatesTags: ["Notification"],
    }),
  }),
});

export const {
  useGetNotificationsQuery,
  useMarkReadMutation,
  useMarkAllReadMutation,
  useDeleteNotificationMutation,
} = notificationsApi;
