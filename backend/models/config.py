# -*- coding: utf-8 -*-
"""
ENVIRONMENT CONFIGURATION VALUES FOR ODMANTIC MODELS AND SIMULATION
"""
import datetime
from dotenv import load_dotenv
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
