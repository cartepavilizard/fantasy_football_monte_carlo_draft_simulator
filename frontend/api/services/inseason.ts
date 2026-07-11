import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import {
  HandcuffFlagsData,
  HandcuffPair,
  HandcuffSeedResult,
  InSeasonEnvelope,
  InSeasonOverview,
  InSeasonSyncSummary,
  LineupData,
  LocksData,
  MatchupsData,
  FreeAgentsData,
  LeagueTransaction,
  PlayoffSosData,
  StreamingData,
  TeamWeekRoster,
  UsageShiftsData,
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
  tagTypes: ["InSeasonOverview", "InSeasonLeague", "Handcuffs"],
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

    // C1/C2/C6: the full lineup call for one team-week — optimal legal
    // lineup, the moves to get there, per-player matchup context, and
    // lock guidance. Mongo-only like every other query in this file.
    getLineup: builder.query<
      InSeasonEnvelope<LineupData | null>,
      { leagueId: number; teamId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, teamId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/lineup`,
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

    getStreaming: builder.query<
      InSeasonEnvelope<StreamingData>,
      { leagueId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/streaming`,
        params: {
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

    // League-independent (C4): every meaningful snap/target-share move
    // vs. each player's trailing baseline for one NFL week. Straight
    // from the ingested PlayerWeekUsage rows — not gated on a league
    // selection like the other endpoints in this file.
    getUsageShifts: builder.query<
      UsageShiftsData,
      { week: number; season?: number }
    >({
      query: ({ week, season }) => ({
        url: `${inseasonUrl}/usage_shifts`,
        params: {
          week,
          ...(season != null && { season }),
        },
      }),
    }),

    // League-independent by default (C5): weeks-14-16 playoff strength of
    // schedule per NFL team, per position. Pass leagueId to additionally
    // join the league's current starters ("rosters" in the response).
    getPlayoffSos: builder.query<
      PlayoffSosData,
      { position?: string; leagueId?: number; season?: number } | void
    >({
      query: (args) => ({
        url: `${inseasonUrl}/playoff_sos`,
        params: {
          ...(args?.position != null && { position: args.position }),
          ...(args?.leagueId != null && { espn_league_id: args.leagueId }),
          ...(args?.season != null && { season: args.season }),
        },
      }),
      providesTags: (result, error, args) =>
        args?.leagueId != null
          ? [{ type: "InSeasonLeague", id: args.leagueId }]
          : [],
    }),

    // C7: flagged handcuffs for one league-week — the curated map joined
    // against rostered starters + the free-agent pool, with C9's homer
    // check attached. Mongo-only like every other query in this file.
    getLeagueHandcuffs: builder.query<
      InSeasonEnvelope<HandcuffFlagsData>,
      { leagueId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/handcuffs`,
        params: {
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    // The curated starter -> direct-backup map (C7), unscoped by league —
    // what the handcuff panel's CRUD reads and writes.
    getHandcuffs: builder.query<{ handcuffs: HandcuffPair[] }, void>({
      query: () => ({ url: `${inseasonUrl}/handcuffs` }),
      providesTags: ["Handcuffs"],
    }),

    setHandcuff: builder.mutation<
      HandcuffPair,
      { starterName: string; handcuffName: string; nflTeam?: string; note?: string }
    >({
      query: ({ starterName, handcuffName, nflTeam, note }) => ({
        url: `${inseasonUrl}/handcuffs`,
        method: "POST",
        params: {
          starter_name: starterName,
          handcuff_name: handcuffName,
          ...(nflTeam && { nfl_team: nflTeam }),
          ...(note && { note }),
        },
      }),
      invalidatesTags: ["Handcuffs", "InSeasonLeague"],
    }),

    seedHandcuffs: builder.mutation<HandcuffSeedResult, void>({
      query: () => ({ url: `${inseasonUrl}/handcuffs/seed`, method: "POST" }),
      invalidatesTags: ["Handcuffs", "InSeasonLeague"],
    }),

    deleteHandcuff: builder.mutation<{ deleted: string }, { starterName: string }>({
      query: ({ starterName }) => ({
        url: `${inseasonUrl}/handcuffs/${encodeURIComponent(starterName)}`,
        method: "DELETE",
      }),
      invalidatesTags: ["Handcuffs", "InSeasonLeague"],
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
  useGetLineupQuery,
  useGetStreamingQuery,
  useGetLocksQuery,
  useGetPlayoffSosQuery,
  useGetUsageShiftsQuery,
  useGetLeagueHandcuffsQuery,
  useGetHandcuffsQuery,
  useSetHandcuffMutation,
  useSeedHandcuffsMutation,
  useDeleteHandcuffMutation,
  useSyncLeagueMutation,
} = inseasonApi;
