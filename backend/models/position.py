# -*- coding: utf-8 -*-
"""
ODMANTIC MODELS FOR POSITIONS
"""
from .config import (
    DST_SIZE,
    FLEX_SIZE,
    K_SIZE,
    QB_SIZE,
    RB_SIZE,
    ROSTER_SIZE,
    TE_SIZE,
    WR_SIZE,
)
from odmantic import EmbeddedModel
from pydantic import BaseModel
from typing import List


class PositionSizes(EmbeddedModel):
    """
    The number of players that can be starters for each position,
    which defaults to environment variables
    """

    qb: int = QB_SIZE
    rb: int = RB_SIZE
    wr: int = WR_SIZE
    te: int = TE_SIZE
    flex: int = FLEX_SIZE
    dst: int = DST_SIZE
    k: int = K_SIZE


class PositionTierDistributions(EmbeddedModel):
    """
    Distributions for the generation of random point projections
    for each position by tier (3 tiers for qb, rb, wr, te)
    """

    qb1: List[float] = []
    qb2: List[float] = []
    qb3: List[float] = []
    rb1: List[float] = []
    rb2: List[float] = []
    rb3: List[float] = []
    wr1: List[float] = []
    wr2: List[float] = []
    wr3: List[float] = []
    te1: List[float] = []
    te2: List[float] = []
    te3: List[float] = []


class PositionMaxPoints(EmbeddedModel):
    """
    Maximum number of projected points for each position, which
    if not set, allows the random projection to be (maybe unrealistically) extreme
    """

    qb: float = 0
    rb: float = 0
    wr: float = 0
    te: float = 0
    dst: float = 0
    k: float = 0


class PositionTiers(BaseModel):
    """
    The index of the last player (when sorted by projected points)
    for each position tier, which are by default based on a
    1QB, 2RB, 2WR, 1TE, 1FLEX, 1DST, 1K roster
    """

    qb: dict = {
        "1": QB_SIZE * ROSTER_SIZE,  # 14 in a 14-team league
        "2": QB_SIZE * 2.5 * ROSTER_SIZE,  # 35
    }
    rb: dict = {
        "1": RB_SIZE * 0.5 * ROSTER_SIZE,  # 14
        "2": RB_SIZE * 2.5 * ROSTER_SIZE,  # 70
    }
    wr: dict = {
        "1": WR_SIZE * 0.5 * ROSTER_SIZE,  # 14
        "2": WR_SIZE * 2.5 * ROSTER_SIZE,  # 70
    }
    te: dict = {
        "1": TE_SIZE * ROSTER_SIZE,  # 14
        "2": TE_SIZE * 2 * ROSTER_SIZE,  # 28
    }
    k: dict = {"1": K_SIZE * ROSTER_SIZE, "2": K_SIZE * 2 * ROSTER_SIZE}
    dst: dict = {"1": DST_SIZE * ROSTER_SIZE, "2": DST_SIZE * 2 * ROSTER_SIZE}
