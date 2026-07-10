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

export type MonteCarloResults = {
  qb: number;
  rb: number;
  wr: number;
  te: number;
  dst: number;
  k: number;
  iterations: number;
};

// Draft results are just an object of each team name with a number (score) as value
export type DraftResults = Record<string, number>;

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
