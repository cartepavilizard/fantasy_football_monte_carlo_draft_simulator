# -*- coding: utf-8 -*-
"""
ENVIRONMENT CONFIGURATION VALUES FOR ODMANTIC MODELS AND SIMULATION
"""
import datetime
from dotenv import load_dotenv
import json
import os

load_dotenv()

# Indicate whether the app is running locally or in Docker
LOCAL = os.getenv("LOCAL", "true").lower() == "true"

# Roster and position sizes
ROSTER_SIZE = int(os.getenv("ROSTER_SIZE", 12))
QB_SIZE = int(os.getenv("QB_SIZE", 1))
RB_SIZE = int(os.getenv("RB_SIZE", 2))
WR_SIZE = int(os.getenv("WR_SIZE", 2))
TE_SIZE = int(os.getenv("TE_SIZE", 1))
FLEX_SIZE = int(os.getenv("FLEX_SIZE", 1))
DST_SIZE = int(os.getenv("DST_SIZE", 1))
K_SIZE = int(os.getenv("K_SIZE", 1))

# Randomization settings
MAX_RANDOM_ADJUSTMENT = float(os.getenv("MAX_RANDOM_ADJUSTMENT", 0.1))

# Draft settings
DRAFT_YEAR = int(
    os.getenv("DRAFT_YEAR", datetime.datetime.now().year)
)  # Default current year
ROUND_SIZE = int(os.getenv("ROUND_SIZE", 14))
SNAKE_DRAFT = os.getenv("SNAKE_DRAFT", "True").lower() == "true"

# Data source credentials and settings (Phase 0)
# Credentials live in env/.env ONLY — never stored in Mongo
ESPN_S2 = os.getenv("ESPN_S2")  # cookie from a logged-in espn.com session
ESPN_SWID = os.getenv("ESPN_SWID")  # matching SWID cookie, braces included
ESPN_LEAGUE_IDS = [
    int(league_id)
    for league_id in os.getenv("ESPN_LEAGUE_IDS", "").replace(" ", "").split(",")
    if league_id
]  # comma-separated ids of the leagues to ingest history from
YAHOO_CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
YAHOO_CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
YAHOO_REFRESH_TOKEN = os.getenv("YAHOO_REFRESH_TOKEN")
FANTASYPROS_API_KEY = os.getenv("FANTASYPROS_API_KEY")  # optional partner key
DATA_SOURCE_CACHE_DIR = os.getenv("DATA_SOURCE_CACHE_DIR", ".data_source_cache")
DATA_SOURCE_CACHE_TTL_SECONDS = float(
    os.getenv("DATA_SOURCE_CACHE_TTL_SECONDS", 6 * 60 * 60)
)

# Owner tendency profiles in the simulation engine (Phase 4).
# Off = the engine behaves exactly as before profiles existed, even for
# leagues with mapped owners — the A/B switch is this one env var.
USE_OWNER_PROFILES = os.getenv("USE_OWNER_PROFILES", "true").lower() == "true"

# Scheduled rankings refresh (Phase 5). Off by default so dev/test runs
# never fetch on their own; docker-compose turns it on for the deployed
# app. Runtime pause/resume via POST /rankings/schedule (draft-day switch).
RANKINGS_REFRESH_ENABLED = (
    os.getenv("RANKINGS_REFRESH_ENABLED", "false").lower() == "true"
)
RANKINGS_REFRESH_INTERVAL_HOURS = float(
    os.getenv("RANKINGS_REFRESH_INTERVAL_HOURS", 24)
)

# Homer check (Phase A, task A6): the NFL team the user roots for.
# When a suggested pick is from this team, the engine attaches a neutral
# side-by-side comparison against the top alternatives
HOMER_TEAM = os.getenv("HOMER_TEAM", "SEA").upper()

# Tag effects in the suggestion engine (Phase A, task A4).
# my_guy wins ties within max(percent-of-best, floor points) of the best
# candidate's value; sleeper consideration ramps linearly from zero at
# SLEEPER_BOOST_START (fraction of the draft elapsed) to SLEEPER_MAX_BOOST
# at the final round. Selection-time effects only — never simulation scoring.
MY_GUY_TIE_PERCENT = float(os.getenv("MY_GUY_TIE_PERCENT", 0.03))
MY_GUY_TIE_FLOOR_POINTS = float(os.getenv("MY_GUY_TIE_FLOOR_POINTS", 5.0))
SLEEPER_MAX_BOOST = float(os.getenv("SLEEPER_MAX_BOOST", 0.15))
SLEEPER_BOOST_START = float(os.getenv("SLEEPER_BOOST_START", 0.5))

# In-season league sync (Phase B). Data is always served from Mongo;
# these only control how loudly staleness is surfaced and how much of
# the free-agent pool one sync pulls.
INSEASON_STALE_AFTER_HOURS = float(os.getenv("INSEASON_STALE_AFTER_HOURS", 24))
FREE_AGENT_FETCH_LIMIT = int(os.getenv("FREE_AGENT_FETCH_LIMIT", 300))

# Lock reminders (Phase B, task B5): a reminder notification is created
# once `now` enters the lead window before the lock and is deduped by
# (league, season, week, kind) — the Claude Routine that polls
# /notifications/pending delivers it to the phone.
FIRST_LOCK_REMINDER_HOURS = float(os.getenv("FIRST_LOCK_REMINDER_HOURS", 24))
FINAL_LOCK_REMINDER_HOURS = float(os.getenv("FINAL_LOCK_REMINDER_HOURS", 3))

# Scheduled in-season sync (Phase B, task B3). Off by default so dev/test
# runs never fetch on their own; docker-compose turns it on for the
# deployed app. Cadence tightens Wednesday-Sunday (game week) so rosters,
# matchups, and lock reminders stay fresh close to kickoff, and relaxes
# the rest of the week. Runtime pause/resume via POST /inseason/schedule
# (draft-day switch), same as the rankings refresh loop.
INSEASON_SYNC_ENABLED = (
    os.getenv("INSEASON_SYNC_ENABLED", "false").lower() == "true"
)
INSEASON_SYNC_INTERVAL_HOURS = float(os.getenv("INSEASON_SYNC_INTERVAL_HOURS", 24))
INSEASON_SYNC_GAMEDAY_INTERVAL_HOURS = float(
    os.getenv("INSEASON_SYNC_GAMEDAY_INTERVAL_HOURS", 6)
)

# Matchup strength (Phase C, task C2). The observed points-allowed ratio
# is shrunk toward neutral with a prior worth MATCHUP_PRIOR_GAMES weeks
# of evidence (week 1 = fully neutral by construction), and C1 applies
# it as a capped tilt — alpha * (multiplier - 1), never more than
# MATCHUP_MAX_TILT — because ESPN's weekly projections already price
# the opponent to some degree (full weight would double-count).
MATCHUP_PRIOR_GAMES = float(os.getenv("MATCHUP_PRIOR_GAMES", 4))
MATCHUP_TILT_ALPHA = float(os.getenv("MATCHUP_TILT_ALPHA", 0.5))
MATCHUP_MAX_TILT = float(os.getenv("MATCHUP_MAX_TILT", 0.10))

# Lineup optimizer (Phase C, task C1). The Thursday-morning pull syncs
# all leagues then leaves a lineup_review notification, so Thursday
# decisions are made on fresh data; off by default like every scheduled
# fetch. ESPN_MY_TEAMS maps league id -> the user's team id (JSON, e.g.
# '{"111": 3}') so the review can quote that team's optimizer delta;
# leagues missing from the map get a generic review notification.
LINEUP_PULL_ENABLED = os.getenv("LINEUP_PULL_ENABLED", "false").lower() == "true"
LINEUP_PULL_WEEKDAY = int(os.getenv("LINEUP_PULL_WEEKDAY", 3))  # 3 = Thursday
LINEUP_PULL_HOUR = int(os.getenv("LINEUP_PULL_HOUR", 7))  # local time
try:
    ESPN_MY_TEAMS = {
        int(league_id): int(team_id)
        for league_id, team_id in json.loads(
            os.getenv("ESPN_MY_TEAMS", "{}")
        ).items()
    }
except (ValueError, AttributeError):
    print("WARNING: ESPN_MY_TEAMS is not a valid JSON map; ignoring it")
    ESPN_MY_TEAMS = {}

# Lineup-locking strategy (Phase C, task C6). A starter is "early" when
# their kickoff is at least EARLY_LOCK_LEAD_HOURS before the week's
# final lock (Thu/Fri/Sat games and the Wednesday opener qualify; the
# Sunday slate does not). LOCK_FLEX_MARGIN_POINTS is the most projected
# value the margin rule will suggest trading for Sunday flexibility —
# see models/lineup.py for the option-value rationale behind 1.0.
EARLY_LOCK_LEAD_HOURS = float(os.getenv("EARLY_LOCK_LEAD_HOURS", 36))
LOCK_FLEX_MARGIN_POINTS = float(os.getenv("LOCK_FLEX_MARGIN_POINTS", 1.0))

# Usage-shift detection (Phase C, task C4). A shift is CURRENT week vs
# the mean of up to USAGE_BASELINE_MAX_WEEKS prior weeks (at least
# USAGE_BASELINE_MIN_WEEKS of data, so the first possible alert is
# week 3 — one week is noise, not a baseline). Thresholds are absolute
# share-point moves; the floors ignore bottom-of-roster churn (a 3%->9%
# snap player is nobody's pickup). See models/usage_shifts.py for why
# these specific numbers.
USAGE_SNAP_SHIFT_THRESHOLD = float(os.getenv("USAGE_SNAP_SHIFT_THRESHOLD", 0.12))
USAGE_TARGET_SHIFT_THRESHOLD = float(
    os.getenv("USAGE_TARGET_SHIFT_THRESHOLD", 0.07)
)
USAGE_BASELINE_MAX_WEEKS = int(os.getenv("USAGE_BASELINE_MAX_WEEKS", 4))
USAGE_BASELINE_MIN_WEEKS = int(os.getenv("USAGE_BASELINE_MIN_WEEKS", 2))
USAGE_SNAP_FLOOR = float(os.getenv("USAGE_SNAP_FLOOR", 0.15))
USAGE_TARGET_FLOOR = float(os.getenv("USAGE_TARGET_FLOOR", 0.10))

# Single-game variance flag (Phase C, task C8): distinguishes "quiet box
# score" from "role change" when a player drew real opportunity (targets)
# that didn't turn into catches — process-over-results, not a points
# read. USAGE_VARIANCE_TARGET_FLOOR keeps token-target games out (2
# targets/0 catches isn't a story); USAGE_VARIANCE_CATCH_RATE_CEILING is
# loose enough to catch real bad-luck games (9 targets/3 catches = 33%)
# without flagging an ordinary efficient one.
USAGE_VARIANCE_TARGET_FLOOR = int(os.getenv("USAGE_VARIANCE_TARGET_FLOOR", 6))
USAGE_VARIANCE_CATCH_RATE_CEILING = float(
    os.getenv("USAGE_VARIANCE_CATCH_RATE_CEILING", 0.35)
)

# Playoff strength of schedule (Phase C, task C5). The fantasy-playoff
# window whose opponents get summed against C2's defense_position_strength()
# table; comma-separated so a league running a different bracket can
# override it without a code change.
PLAYOFF_SOS_WEEKS = [
    int(week)
    for week in os.getenv("PLAYOFF_SOS_WEEKS", "14,15,16").replace(" ", "").split(",")
    if week
]

# Trade valuation (Phase E, task E1). The keystone of Phase E: player_value
# (context-free market value, ROS points above replacement) and fit_delta
# (roster-context starting-lineup change) are both in league-scoring ROS
# points, computed over the horizon [latest_scoring_period .. min(
# TRADE_HORIZON_FINAL_WEEK, final_scoring_period or 17)]. Every constant
# here is env-tunable with its rationale in docs/specs/E1-trade-valuation.md
# — no ML, no calibration, no re-projection.
#   TRADE_HORIZON_FINAL_WEEK: value stops at the fantasy championship
#     (week 17 is worthless), defaults to the last playoff-SOS week.
#   TRADE_RATE_WEEKS: trailing ESPN weekly projections averaged into the
#     neutral healthy rate; RATE_MIN_POINTS floors out absence-encoding
#     near-zeros so an injured star is not averaged down to nothing.
#   REPLACEMENT_RANK: the 3rd-best free agent at a position is the zero
#     line (the top FA is often stale/gone; 3rd is reliably attainable).
#   QUESTIONABLE/DOUBTFUL_PLAY_PROB, IR_RETURN_WEEKS, IR_RETURN_DISCOUNT:
#     the availability curve (this IS the IR-stash value).
#   BENCH_FACTOR: bench depth converts to starts via injuries/byes at ~the
#     league's weekly starter-miss rate; without it every consolidation
#     grades free, at full weight hoarding grades free.
#   FAIR_GAP_POINTS / FAIR_GAP_FRACTION: a trade is "fair" when the market
#     gap is within max(absolute, fraction * larger side) — small trades
#     don't fail on trivial absolute gaps, big ones don't on relative ones.
TRADE_HORIZON_FINAL_WEEK = int(
    os.getenv("TRADE_HORIZON_FINAL_WEEK", max(PLAYOFF_SOS_WEEKS))
)
TRADE_RATE_WEEKS = int(os.getenv("TRADE_RATE_WEEKS", 4))
RATE_MIN_POINTS = float(os.getenv("RATE_MIN_POINTS", 0.5))
REPLACEMENT_RANK = int(os.getenv("REPLACEMENT_RANK", 3))
QUESTIONABLE_PLAY_PROB = float(os.getenv("QUESTIONABLE_PLAY_PROB", 0.75))
DOUBTFUL_PLAY_PROB = float(os.getenv("DOUBTFUL_PLAY_PROB", 0.25))
IR_RETURN_WEEKS = int(os.getenv("IR_RETURN_WEEKS", 3))
IR_RETURN_DISCOUNT = float(os.getenv("IR_RETURN_DISCOUNT", 0.8))
BENCH_FACTOR = float(os.getenv("BENCH_FACTOR", 0.15))
FAIR_GAP_POINTS = float(os.getenv("FAIR_GAP_POINTS", 10.0))
FAIR_GAP_FRACTION = float(os.getenv("FAIR_GAP_FRACTION", 0.15))

# Counterproposal generator (Phase E, task E2). A single-move, anchored
# search over E1's pure evaluation functions — given a lopsided proposal,
# tweak it (ADD/REMOVE/SWAP one player) to close E1's market gap without
# wrecking either roster. A four-stage pruning funnel keeps the expensive
# full-fit evaluation to at most MAX_FINALISTS runs. Every constant is
# env-tunable with its rationale in docs/specs/E2-counterproposal-generator.md.
#   MAX_SIDE_PLAYERS: each side sends 1..3 after a move — ESPN trades above
#     3-for-3 essentially never execute, so bigger packages are noise.
#   GAP_SLACK / GAP_MIN_FRACTION: Stage-1 candidate band — a piece worth far
#     more than the gap overshoots into unfairness the other way (1+SLACK
#     ceiling); one worth a fraction of it closes nothing (MIN_FRACTION floor).
#   SURPLUS_COST_CEILING: an "untouchable" is anyone whose removal drops their
#     roster's one-week (w0) starting lineup by more than this — never offered
#     up as a sweetener/swap-in. One week is a deliberate cheap proxy, not the
#     full horizon, so Stage 1 stays arithmetic.
#   MAX_FINALISTS: the residual-gap survivors that reach the expensive Stage-3
#     full fit_delta evaluation (2xH DP runs each) — the whole funnel exists to
#     cap this number.
#   FIT_FLOOR: discard a counter that actively hurts a roster (either side's
#     fit_delta below this); small negatives survive because need asymmetry is
#     the point of trading.
#   MAX_COUNTERS: at most this many counters returned, diversified.
MAX_SIDE_PLAYERS = int(os.getenv("MAX_SIDE_PLAYERS", 3))
GAP_SLACK = float(os.getenv("GAP_SLACK", 0.5))
GAP_MIN_FRACTION = float(os.getenv("GAP_MIN_FRACTION", 0.25))
SURPLUS_COST_CEILING = float(os.getenv("SURPLUS_COST_CEILING", 1.5))
MAX_FINALISTS = int(os.getenv("MAX_FINALISTS", 12))
FIT_FLOOR = float(os.getenv("FIT_FLOOR", -2.0))
MAX_COUNTERS = int(os.getenv("MAX_COUNTERS", 3))

# Usage ingestion (Phase C, task C4's cheap half): the nflverse CSV pull
# that fills PlayerWeekUsage. Off by default like every scheduled fetch;
# InSeasonScheduler.run_now only ingests + raises usage-shift alerts
# when this is on.
USAGE_INGEST_ENABLED = (
    os.getenv("USAGE_INGEST_ENABLED", "false").lower() == "true"
)

# Ranking aggregation settings (Phase 1)
SCORING_FORMAT = os.getenv("SCORING_FORMAT", "ppr")  # standard | half_ppr | ppr
try:
    # Per-source blend weights, e.g. '{"espn": 1.0, "sleeper": 0.5}'
    # (sources missing from the map default to weight 1.0)
    RANKING_BLEND_WEIGHTS = json.loads(os.getenv("RANKING_BLEND_WEIGHTS", "{}"))
except ValueError:
    print("WARNING: RANKING_BLEND_WEIGHTS is not valid JSON; using equal weights")
    RANKING_BLEND_WEIGHTS = {}
