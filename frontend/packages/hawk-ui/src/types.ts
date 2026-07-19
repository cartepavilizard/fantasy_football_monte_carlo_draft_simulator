// Hawk UI's prop-facing types. These re-export the specific slice of the
// app's real API-response types (frontend/types/index.ts) that Hawk UI
// components need — a single source of truth (no hand-duplicated,
// driftable copies), while giving package-internal files a stable local
// import path (`../types`) instead of reaching across the package
// boundary to `@/types`. If this package is ever published standalone,
// this file becomes the one place to swap re-exports for hand-authored
// structural types.
export type {
  League,
  LeagueSimple,
  Team,
  PickLogEntry,
  Player,
  Players,
  PlayerTag,
  MonteCarloResults,
  SuggestedPick,
  HomerCheck,
  PositionScarcity,
  ScarcityCall,
  PlayerAvailability,
  LineupData,
  LineupPlayer,
  LineupSlotEntry,
  HandcuffFlag,
  MatchupsData,
  PlayoffSosData,
  StreamingData,
  WeeklyMatchup,
  RosterSlotEntry,
  TradePlayerValue,
  TradeEvaluation,
  TradeVerdict,
  TradeCounter,
  Notification,
  UsageVariance,
} from "../../../types";
