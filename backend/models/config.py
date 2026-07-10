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

# Ranking aggregation settings (Phase 1)
SCORING_FORMAT = os.getenv("SCORING_FORMAT", "ppr")  # standard | half_ppr | ppr
try:
    # Per-source blend weights, e.g. '{"espn": 1.0, "sleeper": 0.5}'
    # (sources missing from the map default to weight 1.0)
    RANKING_BLEND_WEIGHTS = json.loads(os.getenv("RANKING_BLEND_WEIGHTS", "{}"))
except ValueError:
    print("WARNING: RANKING_BLEND_WEIGHTS is not valid JSON; using equal weights")
    RANKING_BLEND_WEIGHTS = {}
