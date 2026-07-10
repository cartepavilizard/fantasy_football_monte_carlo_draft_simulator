import { SVGProps } from "react";

export type IconSvgProps = SVGProps<SVGSVGElement> & {
  size?: number;
};

export type LeagueSimple = {
  id: string;
  name: string;
  created: string;
};

export type DraftSimple = {
  id: string;
  created: string;
};

// A player carries at most one tag at a time (A3); mirrors
// backend/models/player.py PlayerTag
export type PlayerTag = "sleeper" | "my_guy" | "avoid";

export type Player = {
  name: string;
  position: string;
  nfl_team: string;
  drafted: boolean;
  position_tier: string;
  // Cross-source consensus fields; null on the CSV upload path
  adp?: number | null;
  consensus_rank?: number | null;
  tier?: number | null;
  // User-set tag (A3); null/undefined means untagged
  tag?: PlayerTag | null;
};

export type Players = {
  qb: Player[];
  rb: Player[];
  wr: Player[];
  te: Player[];
  dst: Player[];
  k: Player[];
};

export type Team = {
  name: string;
  owner: string;
  simulator: boolean;
};

// Expand the LeagueSimple type to include the teams and players
export type League = LeagueSimple & {
  teams: Team[];
  players: Players;
  draft_order: number[];
  current_draft_turn: number;
};

// Expand the DraftSimple type to include the league
export type Draft = DraftSimple & {
  league: League;
};

// The tag-aware player the engine would take at a position, and why
// (A4); mirrors backend/models/suggestions.py SuggestedPick
export type SuggestedPick = {
  name: string;
  tag: PlayerTag | null;
  reason: string;
};

export type MonteCarloResults = {
  qb: number;
  rb: number;
  wr: number;
  te: number;
  dst: number;
  k: number;
  iterations: number;
  suggested: Record<string, SuggestedPick>;
};

// Draft results are just an object of each team name with a number (score) as value
export type DraftResults = Record<string, number>;

// Tier-depletion scarcity (GET /draft/:id/scarcity); shapes mirror
// backend/models/scarcity.py
export type ScarcityCall =
  | "reach"
  | "wait"
  | "toss_up"
  | "last_chance"
  | "exhausted"
  | "no_tiers";

// One active-tier player with simulated survival odds
export type PlayerAvailability = {
  name: string;
  tier: number | null;
  projected_points: number;
  survival_at_pick: number; // P(still available at your upcoming pick)
  survival_at_next_pick: number; // P(still available one pick later)
};

// Depletion state and the directional call for one position
export type PositionScarcity = {
  position: string;
  call: ScarcityCall;
  message: string;
  tier: number | null; // the active (best occupied) tier
  remaining_now: number; // true undrafted count in the active tier
  expected_at_pick: number;
  expected_at_next_pick: number;
  prob_tier_at_pick: number;
  prob_tier_at_next_pick: number;
  next_tier: number | null;
  next_tier_remaining_now: number;
  next_tier_expected_at_next_pick: number;
  at_risk: PlayerAvailability[];
};

// Scarcity calls for every position at the simulator's upcoming pick
export type ScarcityReport = {
  current_pick: number;
  your_pick: number;
  your_next_pick: number | null;
  on_the_clock: boolean;
  final_pick: boolean;
  iterations: number;
  elapsed_seconds: number;
  positions: PositionScarcity[];
};

// One source's most recent fetch, as reported by /rankings/status
export type BatchStats = {
  success: boolean;
  error: string | null;
  records: number;
  resolved: number;
  unresolved: number;
  fetched_at: string;
};

export type SourceStatus = {
  kind: "pull" | "push";
  configured: boolean;
  access_mode?: string;
  age_seconds: number | null;
  last_attempt: BatchStats | null;
  last_success: BatchStats | null;
};

export type RankingsStatus = {
  season: number;
  scoring_format: string;
  sources: Record<string, SourceStatus>;
  blend_weights: Record<string, number>;
  blend: {
    generated_at: string;
    sources_used: string[];
    records: number;
  } | null;
};

export type RefreshSummary = {
  season: number;
  scoring_format: string;
  sources: Record<
    string,
    {
      success: boolean;
      error: string | null;
      records: number;
      unresolved: number;
    }
  >;
  blend: { id: string; sources_used: string[]; records: number };
};

export type UdkSummary = {
  source: string;
  batch: { records: number; unresolved: number; anchored: boolean };
  blend: { sources_used: string[]; records: number };
  warning?: string;
};

export type ScheduleStatus = {
  enabled: boolean;
  running: boolean;
  interval_hours: number;
  next_run: string | null;
  last_run: string | null;
  last_error: string | null;
};
