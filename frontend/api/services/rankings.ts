import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import {
  RankingsStatus,
  RefreshSummary,
  ScheduleStatus,
  UdkSummary,
} from "@/types";

// Operations for the automated ranking aggregation pipeline: on-demand
// refresh, per-source freshness, the UDK file drop, and the schedule
export const rankingsApi = createApi({
  reducerPath: "rankingsApi",
  baseQuery: fetchBaseQuery(baseQuery),
  tagTypes: ["Rankings", "Schedule"],
  endpoints: (builder) => ({
    getRankingsStatus: builder.query<RankingsStatus, void>({
      query: () => "/rankings/status",
      providesTags: ["Rankings"],
    }),

    refreshRankings: builder.mutation<RefreshSummary, void>({
      query: () => ({
        url: "/rankings/refresh",
        method: "POST",
      }),
      invalidatesTags: ["Rankings"],
    }),

    uploadUdk: builder.mutation<UdkSummary, { file: File }>({
      query: ({ file }) => {
        var bodyFormData = new FormData();

        bodyFormData.append("contentType", file.type);
        bodyFormData.append("file", file);

        return {
          url: "/rankings/udk",
          method: "POST",
          body: bodyFormData,
        };
      },
      invalidatesTags: ["Rankings"],
    }),

    getSchedule: builder.query<ScheduleStatus, void>({
      query: () => "/rankings/schedule",
      providesTags: ["Schedule"],
    }),

    setSchedule: builder.mutation<
      ScheduleStatus,
      { enabled?: boolean; interval_hours?: number }
    >({
      query: (params) => ({
        url: "/rankings/schedule",
        method: "POST",
        params,
      }),
      invalidatesTags: ["Schedule"],
    }),
  }),
});

export const {
  useGetRankingsStatusQuery,
  useRefreshRankingsMutation,
  useUploadUdkMutation,
  useGetScheduleQuery,
  useSetScheduleMutation,
} = rankingsApi;
