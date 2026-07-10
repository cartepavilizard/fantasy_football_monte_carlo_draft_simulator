import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import { baseQuery } from "@/api/services/base";
import { ScarcityReport } from "@/types";

// Tier-depletion scarcity: reach-vs-wait calls for the simulator's
// upcoming pick. The endpoint runs a ~10s Monte Carlo, so it is only
// fetched through the lazy hook on an explicit refresh — never on render.
export const scarcityApi = createApi({
  reducerPath: "scarcityApi",
  baseQuery: fetchBaseQuery(baseQuery),
  tagTypes: ["Scarcity"],
  endpoints: (builder) => ({
    getScarcity: builder.query<
      ScarcityReport,
      { id: string; seconds?: number }
    >({
      query: ({ id, seconds }) => ({
        url: `/draft/${id}/scarcity`,
        params: seconds != null ? { seconds } : undefined,
      }),
      providesTags: ["Scarcity"],
    }),
  }),
});

export const { useLazyGetScarcityQuery } = scarcityApi;
