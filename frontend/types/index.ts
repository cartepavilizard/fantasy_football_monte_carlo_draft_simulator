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

// One row of the homer-check comparison table (A6); mirrors
// backend/models/homer.py ComparisonPlayer
export type ComparisonPlayer = {
  name: string;
  nfl_team: string;
  projected_points: number;
  consensus_rank: number | null;
  adp: number | null;
  adp_vs_pick: number | null;
  tier: number | null;
  tag: PlayerTag | null;
};

// Neutral side-by-side comparison of a homer-team (Seahawks) suggested
// pick vs. the top alternatives at that position (A6); mirrors
// backend/models/homer.py HomerCheck. No recommendation field, by design.
export type HomerCheck = {
  position: string;
  homer_team: string;
  pick_number: number | null;
  suggested: ComparisonPlayer;
  alternatives: ComparisonPlayer[];
  projection_gap: number;
  market_gap: number | null;
  note: string;
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
  // A6: present only for positions whose suggested pick is a homer-team
  // (Seahawks) player
  homer_checks: Record<string, HomerCheck>;
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

// --- In-season (Phase B, B4): cached-only reads mirroring
// backend/models/inseason.py and backend/inseason_api.py's envelope ---

export type FreshnessSection = {
  last_success_at: string | null;
  last_attempt_at: string | null;
  last_error: string | null;
  error_kind: string | null;
  age_seconds: number | null;
  stale: boolean;
};

// Per-league freshness from league_freshness(); attached to every
// /inseason/* response so stale/auth-expired cache never looks fresh
export type LeagueFreshness = {
  espn_league_id: number;
  season: number;
  sections: Record<string, FreshnessSection>;
  stale: boolean;
  auth_expired: boolean;
  warnings: string[];
};

// The envelope every /inseason/* GET (besides /overview) returns
export type InSeasonEnvelope<T> = {
  data: T;
  freshness: LeagueFreshness;
  warnings: string[];
};

export type LeagueTeamInfo = {
  espn_team_id: number;
  name: string;
  abbrev: string | null;
  owner_guid: string | null;
  owner_name: string | null;
  wins: number;
  losses: number;
  ties: number;
  points_for: number;
  points_against: number;
};

export type InSeasonLeague = {
  espn_league_id: number;
  season: number;
  name: string;
  team_count: number;
  current_matchup_period: number;
  latest_scoring_period: number;
  final_scoring_period: number | null;
  trade_deadline: string | null;
  lineup_slot_counts: Record<string, number>;
  teams: LeagueTeamInfo[];
  synced_at: string;
};

export type InSeasonOverviewEntry = {
  league: InSeasonLeague;
  freshness: LeagueFreshness;
  warnings: string[];
};

// GET /inseason/overview — the league selector + team dropdown's only
// data source; zero external fetches
export type InSeasonOverview = {
  season: number;
  leagues: InSeasonOverviewEntry[];
};

export type RosterSlotEntry = {
  player_id: number;
  player_name: string;
  position: string | null;
  nfl_team: string | null;
  lineup_slot: string;
  injury_status: string | null;
  projected_points: number | null;
  actual_points: number | null;
};

export type TeamWeekRoster = {
  espn_league_id: number;
  season: number;
  week: number;
  espn_team_id: number;
  entries: RosterSlotEntry[];
  synced_at: string;
};

export type WeeklyMatchup = {
  espn_league_id: number;
  season: number;
  week: number;
  home_team_id: number;
  away_team_id: number | null;
  home_points: number;
  away_points: number;
  winner: "home" | "away" | "tie" | null;
  is_playoff: boolean;
  synced_at: string;
};

export type MatchupsData = {
  week: number;
  matchups: WeeklyMatchup[];
};

export type TransactionItem = {
  player_id: number;
  player_name: string | null;
  item_type: string;
  from_team_id: number | null;
  to_team_id: number | null;
};

export type LeagueTransaction = {
  espn_league_id: number;
  season: number;
  espn_transaction_id: string;
  type: string;
  status: string;
  week: number | null;
  team_id: number | null;
  bid_amount: number | null;
  processed_at: string | null;
  items: TransactionItem[];
  synced_at: string;
};

export type FreeAgentEntry = {
  player_id: number;
  player_name: string;
  position: string | null;
  nfl_team: string | null;
  injury_status: string | null;
  percent_owned: number | null;
  projected_points: number | null;
  season_projection: number | null;
};

export type FreeAgentsData = {
  week: number;
  free_agents: FreeAgentEntry[];
};

export type WeekLocks = {
  first_lock: string;
  final_lock: string;
  first_game: string;
  team_locks: Record<string, string>;
};

export type LocksData = {
  week: number;
  locks: WeekLocks | null;
};

// A defense-vs-position matchup entry (C2); mirrors strength_for()'s
// return shape in backend/models/matchup_strength.py
export type MatchupEntry = {
  multiplier: number;
  observed_ratio: number | null;
  weeks_sampled: number;
  confidence: "none" | "low" | "medium" | "high";
  rank: number | null;
};

// One ranked K/DST streaming row (C3); mirrors streaming_recommendations()
// in backend/models/streaming.py. homer_check is C9's neutral comparison,
// present only when nfl_team is the homer team (Seahawks).
export type StreamingRecommendation = {
  player_id: number;
  player_name: string;
  position: string;
  nfl_team: string | null;
  opponent: string | null;
  projected_points: number | null;
  matchup_adjusted_points: number | null;
  matchup: MatchupEntry;
  rank: number;
  homer_check: HomerCheck | null;
};

export type StreamingData = {
  week: number;
  recommendations: StreamingRecommendation[];
};

// C8: single-game variance flag — real opportunity (targets) that
// didn't turn into catches in one game. mirrors variance_note()'s dict
// shape in backend/models/usage_shifts.py. null unless the game clears
// the backend's target floor and catch-rate ceiling; the framing text
// itself lives in the shared VarianceFlag component, not here, since
// the backend only decides whether a game clears the bar.
export type UsageVariance = {
  targets: number;
  receptions: number;
  catch_rate: number; // 0..1
};

// One meaningful usage shift (C4): current-week snap/target share vs a
// 2-4-week trailing baseline. mirrors detect_usage_shifts()'s dict shape
// in backend/models/usage_shifts.py. League-independent — GET
// /inseason/usage_shifts returns these directly, no InSeasonEnvelope.
export type UsageShift = {
  player_name: string;
  position: string | null;
  nfl_team: string | null;
  season: number;
  week: number;
  metric: "snap_share" | "target_share";
  metric_phrase: string;
  current: number; // 0..1
  baseline: number; // 0..1
  delta: number; // current - baseline
  direction: "rising" | "falling";
  baseline_weeks: number;
  variance: UsageVariance | null;
};

export type UsageShiftsData = {
  season: number;
  week: number;
  shifts: UsageShift[];
};

// C5: one NFL team's playoff-window (weeks 14-16) schedule strength for
// one position; mirrors playoff_schedule_strength()'s per-team entry in
// backend/models/playoff_sos.py. score is the SUM of C2's multipliers
// across scheduled opponents — a bye contributes nothing, not an average
// pulled toward neutral, so bye_weeks/games_scheduled explain a low score.
export type PlayoffSosOpponent = {
  week: number;
  opponent: string;
  multiplier: number;
  confidence: "none" | "low" | "medium" | "high";
};

export type PlayoffSosEntry = {
  score: number;
  games_scheduled: number;
  bye_weeks: number[];
  opponents: PlayoffSosOpponent[];
  confidence: "none" | "low" | "medium" | "high";
  rank: number;
};

// One fantasy starter joined against the table above (nfl_team +
// position) — null playoff_sos means the team has no schedule data yet.
export type PlayoffSosStarter = {
  player_name: string;
  position: string;
  nfl_team: string | null;
  lineup_slot: string;
  playoff_sos: PlayoffSosEntry | null;
};

export type PlayoffSosRosterTeam = {
  espn_team_id: number;
  team_name: string | null;
  starters: PlayoffSosStarter[];
  average_rank: number | null;
};

// GET /inseason/playoff_sos: league-independent by default (positions),
// `rosters` only present when scoped with espn_league_id. `note` is C2's
// own early-season "all neutral" note, carried through unchanged.
export type PlayoffSosData = {
  season: number;
  weeks: number[];
  positions: Record<string, Record<string, PlayoffSosEntry>>;
  note: string | null;
  rosters?: PlayoffSosRosterTeam[];
};

// One roster player as annotated by the lineup optimizer (C1/C2/C6):
// projections plus C2's matchup tilt and the kickoff used for C6's lock
// rules. mirrors the `annotated` dict built in optimize_lineup() in
// backend/models/lineup.py.
export type LineupPlayer = {
  player_id: number;
  player_name: string;
  position: string | null;
  nfl_team: string | null;
  injury_status: string | null;
  current_slot: string;
  base_projection: number | null;
  adjusted_projection: number | null;
  opponent: string | null;
  on_bye: boolean;
  kickoff: string | null;
  matchup: MatchupEntry;
};

// One starting slot in the optimal lineup; player is null only when
// nothing eligible remained to fill it.
export type LineupSlotEntry = {
  slot: string;
  player: LineupPlayer | null;
};

// One slot change between the current lineup and the optimal one
export type LineupMove = {
  player_id: number;
  player_name: string;
  from_slot: string;
  to_slot: string;
};

// C6 rule 2 (advice only, never auto-applied): a bench alternative that
// keeps a slot open past an early-locking starter's kickoff, and what
// it costs in projected points. mirrors lock_advice()'s dict shape in
// backend/models/lineup.py. start/alternative are player_ids.
export type LineupLockAdvice = {
  slot: string;
  start: number;
  alternative: number;
  cost_points: number;
  note: string;
};

// GET /inseason/league/{id}/lineup's data (C1): the optimal legal
// lineup from ESPN weekly projections + C2's matchup tilt, the moves to
// get there from the current lineup, and C6's lock guidance. mirrors
// optimize_lineup()'s return dict in backend/models/lineup.py; null
// when no synced roster exists for that team-week.
export type LineupData = {
  week: number;
  espn_team_id: number;
  optimal: LineupSlotEntry[];
  bench: LineupPlayer[];
  ir: LineupPlayer[];
  current_total: number;
  optimal_total: number;
  delta_points: number;
  moves: LineupMove[];
  lock_advice: LineupLockAdvice[];
  warnings: string[];
};

// The curated starter -> direct-backup mapping (C7); mirrors HandcuffPair
// in backend/models/handcuffs.py. source "seed" survives the pre-season
// curation pass, "manual" once a user edits/repoints/deletes it.
export type HandcuffPair = {
  starter_name: string;
  handcuff_name: string;
  nfl_team: string | null;
  position: string;
  note: string | null;
  source: "seed" | "manual";
  active: boolean;
  updated_at: string;
};

export type HandcuffSeedResult = {
  created: number;
  skipped: number;
};

// One flagged handcuff for a league-week (C7): a rostered starter whose
// curated backup is sitting in the free-agent pool. mirrors
// available_handcuff_flags()'s dict shape in backend/models/handcuffs.py.
// priority "high" only when the starter is questionable/doubtful/out;
// homer_check (C9) present only when the handcuff plays for HOMER_TEAM.
export type HandcuffFlag = {
  starter_name: string;
  handcuff_name: string;
  nfl_team: string | null;
  starter_team_id: number;
  starter_injury_status: string | null;
  handcuff_projected_points: number | null;
  handcuff_percent_owned: number | null;
  priority: "high" | "normal";
  homer_check: HomerCheck | null;
};

export type HandcuffFlagsData = {
  week: number;
  handcuffs: HandcuffFlag[];
};

// The curated team -> beat-writer directory (D1); mirrors BeatWriter in
// backend/models/beat_writers.py. source "seed" survives the pre-season
// curation pass, "manual" once a user edits/repoints/deletes it.
export type BeatWriter = {
  nfl_team: string;
  writer_name: string;
  outlet: string;
  note: string | null;
  source: "seed" | "manual";
  active: boolean;
  updated_at: string;
};

export type BeatWriterSeedResult = {
  created: number;
  skipped: number;
};

// D3's generated Grok prompt (GET /inseason/grok_prompt) — the exact
// string to paste into a free xAI account, plus which team it resolved.
export type GrokPrompt = {
  prompt_text: string;
  nfl_team: string | null;
  kind: "beat_check" | "injury_timeline" | "usage_context";
};

// Shared shape of both the parse-preview response and the parsed half
// of a saved PlayerNote (backend/models/player_notes.py's parser output
// plus its two skepticism checks; #3's quarantine is structural).
export type GrokParsePreview = {
  parsed_block: boolean;
  player: string | null;
  status_signal: "upgrade" | "downgrade" | "unchanged" | "unclear" | null;
  summary: string | null;
  sources: string[];
  newest_source_date: string | null;
  confidence: "reported" | "rumored" | "speculation" | null;
  stale_risk: boolean;
  conflicts: string[];
};

// One saved manual Grok paste-back (D3); mirrors PlayerNote in
// backend/models/player_notes.py. verified is always false — no code
// path sets it true, by design (the human reading the note decides).
// id is a string ObjectId — unlike most in-season types this one is
// exposed, since deleting a note needs it (no other natural key).
export type PlayerNote = {
  id: string;
  season: number;
  week: number;
  player_name: string;
  nfl_team: string | null;
  kind: "beat_check" | "injury_timeline" | "usage_context";
  prompt_text: string;
  raw_text: string;
  summary: string | null;
  status_signal: "upgrade" | "downgrade" | "unchanged" | "unclear" | null;
  grok_confidence: "reported" | "rumored" | "speculation" | null;
  sources: string[];
  newest_source_date: string | null;
  parsed_block: boolean;
  stale_risk: boolean;
  conflicts: string[];
  verified: false;
  created_at: string;
};

// POST /inseason/sync — the one route that talks to ESPN; loose section
// typing since counts vary (teams/matchups/players/transactions)
export type SyncSectionResult = {
  success: boolean;
  error?: string;
  error_kind?: string;
  [key: string]: unknown;
};

export type LeagueSyncSummary = {
  espn_league_id: number;
  season: number;
  week: number | null;
  sections: Record<string, SyncSectionResult>;
};

export type InSeasonSyncSummary = {
  season: number;
  pro_schedule: SyncSectionResult;
  leagues: Record<string, LeagueSyncSummary>;
  lock_reminders_created: { kind: string; title: string; id: string }[];
};

// --- Notifications (Phase B, B5): mirrors backend/models/notifications.py
// Notification and the panel CRUD in backend/notifications_api.py.
// `read` is panel state (user saw it in-app); `pushed_at` is the
// Routine's ack (delivered to the phone) — the two stay visually
// distinct since a pushed item can still be unread in the panel.
export type Notification = {
  id: string;
  kind: string;
  dedupe_key: string;
  title: string;
  body: string;
  espn_league_id: number | null;
  season: number | null;
  week: number | null;
  event_at: string | null;
  created_at: string;
  read: boolean;
  pushed_at: string | null;
};
