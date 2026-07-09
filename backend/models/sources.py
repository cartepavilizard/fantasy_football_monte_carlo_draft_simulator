# -*- coding: utf-8 -*-
"""
ODMANTIC MODELS FOR EXTERNAL SOURCE DATA (PHASE 0)

Top-level collections for everything pulled from outside the app. These
deliberately live OUTSIDE the League document: League is deep-copied on
every Monte Carlo iteration, so bulk source data embedded there would be
copied hundreds of times per simulation. Only small, precomputed results
(blended projections, owner profiles) ever get materialized into a league.
"""
import datetime
from odmantic import EmbeddedModel, Model
from odmantic import Field as ODField
from typing import Dict, List, Optional


class SourceRankingRecord(EmbeddedModel):
    """One player as one source ranked them on one fetch"""

    raw_name: str  # exactly as the source spelled it
    canonical_name: Optional[str] = None  # None until resolved
    resolution_method: str = "unresolved"
    resolution_confidence: float = 0.0
    position: str
    nfl_team: Optional[str] = None
    rank: Optional[float] = None
    position_rank: Optional[float] = None
    tier: Optional[int] = None
    adp: Optional[float] = None
    projection: Optional[float] = None


class SourceRankingBatch(Model):
    """One fetch of one source: provenance plus its normalized rows"""

    model_config = {"collection": "source_rankings"}

    source: str
    season: int
    scoring_format: str  # standard | half_ppr | ppr
    fetched_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)
    success: bool = True
    error: Optional[str] = None
    records: List[SourceRankingRecord] = []


class BlendedRankingRecord(EmbeddedModel):
    """One player's cross-source consensus value"""

    canonical_name: str
    position: str
    nfl_team: Optional[str] = None
    blended_value: float  # positional z-score blend; the sort key
    blended_projection: Optional[float] = None
    consensus_rank: Optional[float] = None
    adp: Optional[float] = None
    tier: Optional[int] = None
    source_values: Dict[str, float] = {}  # per-source value that fed the blend


class BlendedRanking(Model):
    """One generated blend across all available sources"""

    model_config = {"collection": "blended_rankings"}

    season: int
    scoring_format: str
    generated_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)
    source_weights: Dict[str, float] = {}
    sources_used: List[str] = []
    records: List[BlendedRankingRecord] = []


class HistoricalPick(Model):
    """
    One pick from one real historical draft, per owner — the raw material
    for owner tendency profiling (ESPN-exclusive per Addendum A)
    """

    model_config = {"collection": "historical_picks"}

    espn_league_id: int
    season: int
    overall_pick: int
    round_num: int
    round_pick: int
    member_guid: Optional[str] = None  # ESPN's stable owner id
    espn_team_id: int
    raw_player_name: str
    canonical_name: Optional[str] = None
    position: Optional[str] = None
    is_keeper: bool = False
    bid_amount: Optional[int] = None  # nonzero -> auction season, excluded
    historical_adp: Optional[float] = None  # backfilled from FFC by season
    fetched_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


class OwnerProfile(Model):
    """
    Precomputed tendency metrics for one human owner, merged across
    leagues/seasons via member GUIDs. Metrics are filled in Phase 3; every
    metric must carry its own sample size so the simulation can fall back
    to the generic model below the frequency floor.
    """

    model_config = {"collection": "owner_profiles"}

    profile_key: str  # stable app-side key for this human
    display_names: List[str] = []
    member_guids: List[str] = []
    espn_league_ids: List[int] = []
    seasons_observed: List[int] = []
    total_picks_observed: int = 0
    metrics: dict = {}  # Phase 3: position freq by round bucket, reach stats, ...
    generated_at: Optional[datetime.datetime] = None


class PlayerAlias(Model):
    """
    A persisted manual override for the player resolver: alias (normalized
    spelling from some source) -> canonical name in the player pool
    """

    model_config = {"collection": "player_aliases"}

    alias: str  # store normalized (resolver.normalize_name) form
    canonical_name: str
    position: Optional[str] = None
    note: Optional[str] = None  # why the override exists
    created: datetime.datetime = ODField(default_factory=datetime.datetime.now)
