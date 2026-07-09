import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import { DraftSimple, LeagueSimple } from "@/types";

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
  useCreateDraftMutation,
  useCreateLeagueMutation,
  useAddPlayersMutation,
  useAddHistoricalDraftsMutation,
  useAddHistoricalPlayersMutation,
} = leagueApi;
