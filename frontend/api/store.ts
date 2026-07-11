import { configureStore } from "@reduxjs/toolkit";
import { setupListeners } from "@reduxjs/toolkit/query";

import { draftApi } from "./services/draft";
import { inseasonApi } from "./services/inseason";
import { leagueApi } from "./services/league";
import { notificationsApi } from "./services/notifications";
import { rankingsApi } from "./services/rankings";
import { scarcityApi } from "./services/scarcity";

export const store = configureStore({
  reducer: {
    // Add the generated reducer as a specific top-level slice
    [draftApi.reducerPath]: draftApi.reducer,
    [inseasonApi.reducerPath]: inseasonApi.reducer,
    [leagueApi.reducerPath]: leagueApi.reducer,
    [notificationsApi.reducerPath]: notificationsApi.reducer,
    [rankingsApi.reducerPath]: rankingsApi.reducer,
    [scarcityApi.reducerPath]: scarcityApi.reducer,
  },
  // Adding the api middleware enables caching, invalidation, polling,
  // and other useful features of `rtk-query`.
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware()
      .concat(draftApi.middleware)
      .concat(inseasonApi.middleware)
      .concat(leagueApi.middleware)
      .concat(notificationsApi.middleware)
      .concat(rankingsApi.middleware)
      .concat(scarcityApi.middleware),
});

// optional, but required for refetchOnFocus/refetchOnReconnect behaviors
// see `setupListeners` docs - takes an optional callback as the 2nd arg for customization
setupListeners(store.dispatch);
