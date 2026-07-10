import { configureStore } from "@reduxjs/toolkit";
import { setupListeners } from "@reduxjs/toolkit/query";

import { draftApi } from "./services/draft";
import { leagueApi } from "./services/league";
import { rankingsApi } from "./services/rankings";

export const store = configureStore({
  reducer: {
    // Add the generated reducer as a specific top-level slice
    [draftApi.reducerPath]: draftApi.reducer,
    [leagueApi.reducerPath]: leagueApi.reducer,
    [rankingsApi.reducerPath]: rankingsApi.reducer,
  },
  // Adding the api middleware enables caching, invalidation, polling,
  // and other useful features of `rtk-query`.
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware()
      .concat(draftApi.middleware)
      .concat(leagueApi.middleware)
      .concat(rankingsApi.middleware),
});

// optional, but required for refetchOnFocus/refetchOnReconnect behaviors
// see `setupListeners` docs - takes an optional callback as the 2nd arg for customization
setupListeners(store.dispatch);
