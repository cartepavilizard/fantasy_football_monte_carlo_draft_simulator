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

# Ranking aggregation settings (Phase 1)
SCORING_FORMAT = os.getenv("SCORING_FORMAT", "ppr")  # standard | half_ppr | ppr
try:
    # Per-source blend weights, e.g. '{"espn": 1.0, "sleeper": 0.5}'
    # (sources missing from the map default to weight 1.0)
    RANKING_BLEND_WEIGHTS = json.loads(os.getenv("RANKING_BLEND_WEIGHTS", "{}"))
except ValueError:
    print("WARNING: RANKING_BLEND_WEIGHTS is not valid JSON; using equal weights")
    RANKING_BLEND_WEIGHTS = {}
