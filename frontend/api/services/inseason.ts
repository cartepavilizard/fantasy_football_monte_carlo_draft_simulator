import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import {
  BeatWriter,
  BeatWriterSeedResult,
  BlockingData,
  ByeOutlookData,
  DeadlineReport,
  GrokParsePreview,
  GrokPrompt,
  HandcuffFlagsData,
  HandcuffPair,
  HandcuffSeedResult,
  HoardingReportData,
  InSeasonEnvelope,
  InSeasonOverview,
  InSeasonSyncSummary,
  LineupData,
  LocksData,
  MatchupsData,
  FreeAgentsData,
  LeagueTransaction,
  PlayerNote,
  PlayoffSosData,
  StrategyFlagsData,
  StreamingData,
  TeamWeekRoster,
  TradeCountersResult,
  TradeEvaluation,
  TradeMessageData,
  TradeOpportunityReport,
  TradeProposalBody,
  TradeWillingnessData,
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
  tagTypes: [
    "InSeasonOverview",
    "InSeasonLeague",
    "Handcuffs",
    "BeatWriters",
    "PlayerNotes",
  ],
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

    // E3: per-owner trade-willingness profiles for one league, sorted
    // most-willing first. Mongo-only like every other query in this file.
    getTradeWillingness: builder.query<
      InSeasonEnvelope<TradeWillingnessData>,
      { leagueId: number; season?: number }
    >({
      query: ({ leagueId, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/trade_willingness`,
        params: {
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

    // D1: the curated team -> beat-writer directory, unscoped by league —
    // what the writers panel's CRUD reads and writes, and what D3's
    // beat_check prompt template joins by nfl_team.
    getWriters: builder.query<{ writers: BeatWriter[] }, void>({
      query: () => ({ url: `${inseasonUrl}/writers` }),
      providesTags: ["BeatWriters"],
    }),

    setWriter: builder.mutation<
      BeatWriter,
      { nflTeam: string; writerName: string; outlet: string; note?: string }
    >({
      query: ({ nflTeam, writerName, outlet, note }) => ({
        url: `${inseasonUrl}/writers`,
        method: "POST",
        params: {
          nfl_team: nflTeam,
          writer_name: writerName,
          outlet,
          ...(note && { note }),
        },
      }),
      invalidatesTags: ["BeatWriters"],
    }),

    seedWriters: builder.mutation<BeatWriterSeedResult, void>({
      query: () => ({ url: `${inseasonUrl}/writers/seed`, method: "POST" }),
      invalidatesTags: ["BeatWriters"],
    }),

    deleteWriter: builder.mutation<{ deleted: string }, { nflTeam: string }>({
      query: ({ nflTeam }) => ({
        url: `${inseasonUrl}/writers/${encodeURIComponent(nflTeam)}`,
        method: "DELETE",
      }),
      invalidatesTags: ["BeatWriters"],
    }),

    // D3: the manual Grok bridge. getGrokPrompt is pure string assembly
    // from cached data (no fetch, no LLM call); the paste-back flow is
    // parse (preview, no save) then createPlayerNote (re-parses server
    // side and saves) — the UI never trusts the preview round-trip.
    getGrokPrompt: builder.query<
      GrokPrompt,
      {
        player: string;
        kind: "beat_check" | "injury_timeline" | "usage_context";
        season?: number;
        injury?: string;
        context?: string;
      }
    >({
      query: ({ player, kind, season, injury, context }) => ({
        url: `${inseasonUrl}/grok_prompt`,
        params: {
          player,
          kind,
          ...(season != null && { season }),
          ...(injury && { injury }),
          ...(context && { context }),
        },
      }),
    }),

    parsePlayerNote: builder.mutation<
      GrokParsePreview,
      { rawText: string; playerName?: string; season?: number; week?: number }
    >({
      query: ({ rawText, playerName, season, week }) => ({
        url: `${inseasonUrl}/player_note/parse`,
        method: "POST",
        body: {
          raw_text: rawText,
          player_name: playerName,
          season,
          week,
        },
      }),
    }),

    createPlayerNote: builder.mutation<
      PlayerNote,
      {
        playerName: string;
        kind: "beat_check" | "injury_timeline" | "usage_context";
        promptText: string;
        rawText: string;
        season: number;
        week: number;
        summary?: string;
        statusSignal?: string;
      }
    >({
      query: ({
        playerName,
        kind,
        promptText,
        rawText,
        season,
        week,
        summary,
        statusSignal,
      }) => ({
        url: `${inseasonUrl}/player_note`,
        method: "POST",
        body: {
          player_name: playerName,
          kind,
          prompt_text: promptText,
          raw_text: rawText,
          season,
          week,
          summary,
          status_signal: statusSignal,
        },
      }),
      invalidatesTags: ["PlayerNotes"],
    }),

    getPlayerNotes: builder.query<
      { notes: PlayerNote[] },
      { player?: string; week?: number; season?: number } | void
    >({
      query: (args) => ({
        url: `${inseasonUrl}/player_notes`,
        params: {
          ...(args?.player && { player: args.player }),
          ...(args?.week != null && { week: args.week }),
          ...(args?.season != null && { season: args.season }),
        },
      }),
      providesTags: ["PlayerNotes"],
    }),

    deletePlayerNote: builder.mutation<{ deleted: boolean }, { noteId: string }>({
      query: ({ noteId }) => ({
        url: `${inseasonUrl}/player_note/${encodeURIComponent(noteId)}`,
        method: "DELETE",
      }),
      invalidatesTags: ["PlayerNotes"],
    }),

    // --- Phase E/F trade surfaces ------------------------------------------
    // All Mongo-only (cached-only club). The POSTs carry a proposal body,
    // NOT a fetch trigger — the handler is pure Mongo reads, so they inherit
    // B4's constraint the same way handcuff CRUD does. The Trade Room page
    // drives evaluate/counters/message on demand from its proposal builder.

    // E1: grade a proposal on both value lenses (market fairness + roster
    // fit). On-demand POST — the Trade Room fires it from the Evaluate
    // button, like parsePlayerNote fires from Preview parse.
    evaluateTrade: builder.mutation<
      InSeasonEnvelope<TradeEvaluation>,
      { leagueId: number } & TradeProposalBody
    >({
      query: ({ leagueId, ...body }) => ({
        url: `${inseasonUrl}/league/${leagueId}/trade/evaluate`,
        method: "POST",
        body,
      }),
    }),

    // E2: 1-3 single-move counterproposals that close the gap into E1's fair
    // band without wrecking either roster's fit. Same body as evaluate.
    countersTrade: builder.mutation<
      InSeasonEnvelope<TradeCountersResult>,
      { leagueId: number } & TradeProposalBody
    >({
      query: ({ leagueId, ...body }) => ({
        url: `${inseasonUrl}/league/${leagueId}/trade/counters`,
        method: "POST",
        body,
      }),
    }),

    // E7: render the friendly, non-salesy message for a proposal. A GET —
    // accepts the proposal as comma-separated id query params, mirroring how
    // player_values is a GET. Lazy because the Trade Room only renders it on
    // demand (the Message button), never on mount.
    getTradeMessage: builder.query<
      InSeasonEnvelope<TradeMessageData>,
      {
        leagueId: number;
        teamA: number;
        teamB: number;
        sendsA: number[];
        sendsB: number[];
        week?: number;
        season?: number;
        willingness?: string;
      }
    >({
      query: ({
        leagueId,
        teamA,
        teamB,
        sendsA,
        sendsB,
        week,
        season,
        willingness,
      }) => ({
        url: `${inseasonUrl}/league/${leagueId}/trade/message`,
        params: {
          team_a: teamA,
          team_b: teamB,
          sends_a: sendsA.join(","),
          sends_b: sendsB.join(","),
          ...(week != null && { week }),
          ...(season != null && { season }),
          ...(willingness && { willingness }),
        },
      }),
    }),

    // E4: the on-demand opportunity report — every current injury window the
    // scanner sees, at window or watch severity, with the rival's weekly gap,
    // your surplus pieces, and the E1 probe where one ran. Pure re-evaluation
    // of synced state — refreshing never consumes push budget.
    getTradeOpportunities: builder.query<
      InSeasonEnvelope<TradeOpportunityReport>,
      { leagueId: number; season?: number }
    >({
      query: ({ leagueId, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/trade_opportunities`,
        params: { ...(season != null && { season }) },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    // E6: the STORED weekly post-waivers hoarding report. data is null when
    // no report has been generated yet (the scan runs via the scheduler, not
    // on the read path) — the panel renders an empty state, never crashes.
    getHoarding: builder.query<
      InSeasonEnvelope<HoardingReportData | null>,
      { leagueId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/hoarding`,
        params: {
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    // E5: blocking plays computed on demand — rivals' injured-star handcuffs
    // worth grabbing purely to deny. The join is cheap (bounded rivals'
    // rosters), so unlike E6 it computes on the read path.
    getBlocking: builder.query<
      InSeasonEnvelope<BlockingData>,
      { leagueId: number; week?: number; season?: number }
    >({
      query: ({ leagueId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/blocking`,
        params: {
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    // E8: per-league trade-deadline report — buy/sell window flags per team
    // from trade_deadline + wins/losses, with E1's playoff_value attached
    // where buildable. A league with no trade_deadline returns in_window
    // false and no team flags.
    getDeadlineReport: builder.query<
      InSeasonEnvelope<DeadlineReport>,
      { leagueId: number; season?: number }
    >({
      query: ({ leagueId, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/deadline_report`,
        params: { ...(season != null && { season }) },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    // F1 + F3: stacking + anti-correlation flags for one roster (or every
    // roster when espn_team_id is omitted). Display-only — flags, never
    // rules; removing them changes no ranking, valuation, or verdict.
    getStrategyFlags: builder.query<
      InSeasonEnvelope<StrategyFlagsData>,
      { leagueId: number; teamId?: number; week?: number; season?: number }
    >({
      query: ({ leagueId, teamId, week, season }) => ({
        url: `${inseasonUrl}/league/${leagueId}/strategy_flags`,
        params: {
          ...(teamId != null && { espn_team_id: teamId }),
          ...(week != null && { week }),
          ...(season != null && { season }),
        },
      }),
      providesTags: (result, error, { leagueId }) => [
        { type: "InSeasonLeague", id: leagueId },
      ],
    }),

    // F2: bye planning — the league-wide cluster warning PLUS the per-roster
    // in-season thin-week preview. Degrades to status="no_schedule_data"
    // when no ProGame rows exist; the panel shows that note, not a crash.
    getByeOutlook: builder.query<
      InSeasonEnvelope<ByeOutlookData>,
      {
        leagueId: number;
        teamId?: number;
        week?: number;
        season?: number;
        threshold?: number;
      }
    >({
      query: ({ leagueId, teamId, week, season, threshold }) => ({
        url: `${inseasonUrl}/league/${leagueId}/bye_outlook`,
        params: {
          ...(teamId != null && { espn_team_id: teamId }),
          ...(week != null && { week }),
          ...(season != null && { season }),
          ...(threshold != null && { threshold }),
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
  useGetLineupQuery,
  useGetStreamingQuery,
  useGetTradeWillingnessQuery,
  useGetLocksQuery,
  useGetPlayoffSosQuery,
  useGetUsageShiftsQuery,
  useGetLeagueHandcuffsQuery,
  useGetHandcuffsQuery,
  useSetHandcuffMutation,
  useSeedHandcuffsMutation,
  useDeleteHandcuffMutation,
  useGetWritersQuery,
  useSetWriterMutation,
  useSeedWritersMutation,
  useDeleteWriterMutation,
  useGetGrokPromptQuery,
  useLazyGetGrokPromptQuery,
  useParsePlayerNoteMutation,
  useCreatePlayerNoteMutation,
  useGetPlayerNotesQuery,
  useDeletePlayerNoteMutation,
  useEvaluateTradeMutation,
  useCountersTradeMutation,
  useGetTradeMessageQuery,
  useLazyGetTradeMessageQuery,
  useGetTradeOpportunitiesQuery,
  useGetHoardingQuery,
  useGetBlockingQuery,
  useGetDeadlineReportQuery,
  useGetStrategyFlagsQuery,
  useGetByeOutlookQuery,
  useSyncLeagueMutation,
} = inseasonApi;
