import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import { DraftSimple, LeagueSimple, Player, PlayerTag, Players } from "@/types";

// Url for all league operations
const leagueUrl = "/league";

// Operations for querying the list of available leagues
// and creating a new league
export const leagueApi = createApi({
  reducerPath: "leagueApi",
  baseQuery: fetchBaseQuery(baseQuery),
  tagTypes: ["League"],
  endpoints: (builder) => ({
    getLeagues: builder.query<LeagueSimple[], string>({
      query: () => leagueUrl,
      providesTags: ["League"],
    }),

    // Players in a league (A3), optionally filtered to a tag: sleeper,
    // my_guy, avoid. Defaults to draftable (undrafted) players only.
    getPlayers: builder.query<
      Players,
      { id: string; tag?: PlayerTag; draftableOnly?: boolean }
    >({
      query: ({ id, tag, draftableOnly }) => ({
        url: `${leagueUrl}/${id}/player`,
        params: {
          ...(draftableOnly !== undefined && {
            draftable_only: draftableOnly,
          }),
          ...(tag !== undefined && { tag }),
        },
      }),
      providesTags: ["League"],
    }),

    // Set a player's tag; replaces any existing tag (A3)
    tagPlayer: builder.mutation<
      Player,
      { id: string; name: string; tag: PlayerTag }
    >({
      query: ({ id, name, tag }) => ({
        url: `${leagueUrl}/${id}/player/${encodeURIComponent(name)}/tag`,
        method: "POST",
        params: { tag },
      }),
      invalidatesTags: ["League"],
    }),

    // Clear a player's tag (A3)
    untagPlayer: builder.mutation<Player, { id: string; name: string }>({
      query: ({ id, name }) => ({
        url: `${leagueUrl}/${id}/player/${encodeURIComponent(name)}/tag`,
        method: "DELETE",
      }),
      invalidatesTags: ["League"],
    }),

    // To create a draft for a league, we need to send a POST request to
    // '/league/:id/draft'
    createDraft: builder.mutation<DraftSimple, { id: string }>({
      query: ({ id }) => ({
        url: `${leagueUrl}/${id}/draft`,
        method: "POST",
      }),
    }),

    // To create a league, we need to send a POST request to '/league'
    // with all of the teams data, which is required. Settings are optional;
    // any left unset fall back to the backend's configured defaults.
    createLeague: builder.mutation<
      LeagueSimple,
      {
        name: string;
        teams: File;
        round_size?: number;
        roster_size?: number;
        snake_draft?: boolean;
        qb_size?: number;
        rb_size?: number;
        wr_size?: number;
        te_size?: number;
        flex_size?: number;
        dst_size?: number;
        k_size?: number;
      }
    >({
      query: ({ name, teams, ...settings }) => {
        var bodyFormData = new FormData();

        bodyFormData.append("contentType", teams.type);
        bodyFormData.append("file", teams);

        // Omit unset settings so the backend's own defaults apply
        const params: Record<string, string | number | boolean> = { name };

        Object.entries(settings).forEach(([key, value]) => {
          if (value !== undefined) {
            params[key] = value;
          }
        });

        return {
          url: leagueUrl,
          method: "POST",
          params,
          body: bodyFormData,
        };
      },
      invalidatesTags: ["League"],
    }),

    // Players can instead be materialized from the blended rankings with
    // a POST to '/league/:id/player/sync' — the no-CSV path
    syncPlayers: builder.mutation<void, { id: string }>({
      query: ({ id }) => ({
        url: `${leagueUrl}/${id}/player/sync`,
        method: "POST",
      }),
      invalidatesTags: ["League"],
    }),

    // The opponent-pick regression can be trained from ingested ESPN
    // draft history instead of a CSV
    syncHistoricalDrafts: builder.mutation<
      void,
      { id: string; espnLeagueId: number }
    >({
      query: ({ id, espnLeagueId }) => ({
        url: `${leagueUrl}/${id}/historical_draft/sync`,
        method: "POST",
        params: { espn_league_id: espnLeagueId },
      }),
      invalidatesTags: ["League"],
    }),

    // Players are added from a file POSTed to '/league/:id/player'
    addPlayers: builder.mutation<void, { id: string; players: File }>({
      query: ({ id, players }) => {
        var bodyFormData = new FormData();

        bodyFormData.append("contentType", players.type);
        bodyFormData.append("file", players);

        return {
          url: `${leagueUrl}/${id}/player`,
          method: "POST",
          body: bodyFormData,
        };
      },
      invalidatesTags: ["League"],
    }),

    // Historical players are added from a file POSTed to '/league/:id/historical_player'
    addHistoricalPlayers: builder.mutation<
      void,
      {
        id: string;
        players: File;
      }
    >({
      query: ({ id, players }) => {
        var bodyFormData = new FormData();

        bodyFormData.append("contentType", players.type);
        bodyFormData.append("file", players);

        return {
          url: `${leagueUrl}/${id}/historical_player`,
          method: "POST",
          body: bodyFormData,
        };
      },
      invalidatesTags: ["League"],
    }),

    // Historical drafts are added from a file POSTed to '/league/:id/historical_draft'
    addHistoricalDrafts: builder.mutation<
      void,
      {
        id: string;
        drafts: File;
      }
    >({
      query: ({ id, drafts }) => {
        var bodyFormData = new FormData();

        bodyFormData.append("contentType", drafts.type);
        bodyFormData.append("file", drafts);

        return {
          url: `${leagueUrl}/${id}/historical_draft`,
          method: "POST",
          body: bodyFormData,
        };
      },
      invalidatesTags: ["League"],
    }),
  }),
});

export const {
  useGetLeaguesQuery,
  useGetPlayersQuery,
  useCreateDraftMutation,
  useCreateLeagueMutation,
  useAddPlayersMutation,
  useSyncPlayersMutation,
  useSyncHistoricalDraftsMutation,
  useAddHistoricalDraftsMutation,
  useAddHistoricalPlayersMutation,
  useTagPlayerMutation,
  useUntagPlayerMutation,
} = leagueApi;
