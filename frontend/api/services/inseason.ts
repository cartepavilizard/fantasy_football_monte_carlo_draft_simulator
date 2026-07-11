import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import {
  InSeasonEnvelope,
  InSeasonOverview,
  InSeasonSyncSummary,
  LocksData,
  MatchupsData,
  FreeAgentsData,
  LeagueTransaction,
  TeamWeekRoster,
} from "@/types";

// Url for all in-season operations
const inseasonUrl = "/inseason";

// B4 hard constraint: every query here hits a GET /inseason/* route,
// which is Mongo-cache-only by construction (see backend/inseason_api.py).
// syncLeague is the ONE mutation in this file, and it is the only route
// in the whole app that talks to ESPN — never call it implicitly.
export const inseasonApi = createApi({
  reducerPath: "inseasonApi",
  baseQuery: fetchBaseQuery(baseQuery),
  tagTypes: ["InSeasonOverview", "InSeasonLeague"],
  endpoints: (builder) => ({
    getOverview: builder.query<InSeasonOverview, { season?: number } | void>({
      query: (args) => ({
        url: `${inseasonUrl}/overview`,
        params: args?.season != null ? { season: args.season } : undefined,
      }),
      providesTags: ["InSeasonOverview"],
    }),

    getRoster: builder.query<
      InSeasonEnvelope<TeamWeekRoster | null>,
      { leagueId: number; teamId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, teamId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/roster`,
        params: {
          espn_team_id: teamId,
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    getMatchups: builder.query<
      InSeasonEnvelope<MatchupsData>,
      { leagueId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/matchups`,
        params: {
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    getTransactions: builder.query<
      InSeasonEnvelope<LeagueTransaction[]>,
      { leagueId: number; week?: number; limit?: number; season?: number }
    >({
      query: ({ leagueId, week, limit, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/transactions`,
        params: {
          ...(week != null && { week }),
          ...(limit != null && { limit }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    getFreeAgents: builder.query<
      InSeasonEnvelope<FreeAgentsData>,
      {
        leagueId: number;
        position?: string;
        limit?: number;
        week?: number;
        season?: number;
      }
    >({
      query: ({ leagueId, position, limit, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/free_agents`,
        params: {
          ...(position != null && { position }),
          ...(limit != null && { limit }),
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    getLocks: builder.query<
      InSeasonEnvelope<LocksData>,
      { leagueId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/locks`,
        params: {
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    // The ONLY route in this file that touches ESPN — always an explicit
    // user action ("Sync now"), never triggered by switching league/team.
    syncLeague: builder.mutation<
      InSeasonSyncSummary,
      { leagueId?: number; season?: number }
    >({
      query: ({ leagueId, season }) => ({
        url: `${inseasonUrl}/sync`,
        method: "POST",
        params: {
          ...(leagueId != null && { espn_league_id: leagueId }),
          ...(season != null && { season }),
        },
      }),
      invalidatesTags: ["InSeasonOverview", "InSeasonLeague"],
    }),
  }),
});

export const {
  useGetOverviewQuery,
  useGetRosterQuery,
  useGetMatchupsQuery,
  useGetTransactionsQuery,
  useGetFreeAgentsQuery,
  useGetLocksQuery,
  useSyncLeagueMutation,
} = inseasonApi;
