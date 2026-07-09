# -*- coding: utf-8 -*-
"""
ODMANTIC MODELS FOR PLAYERS
"""
from .config import DRAFT_YEAR, MAX_RANDOM_ADJUSTMENT
from .position import PositionMaxPoints, PositionTierDistributions, PositionTiers
from odmantic import EmbeddedModel
from pydantic import BaseModel, field_validator, model_validator
import random
from typing import Dict, List, Union

# Load the position tiers, determined by environment variables
pt = PositionTiers()


class PlayerPoints(EmbeddedModel):
    """
    Projected vs. actual points for a single player,
    which must be calculated separately and placed on a CSV loaded on main.py
    """

    projected_points: float
    actual_points: Union[float, None] = None  # Will be none for current draft year


class PlayerPointsRandomized(BaseModel):
    """
    Information about a random projection of points for a player,
    including the total adjustment to the original projection
    """

    randomized_points: int
    projected_points: int
    adjustment: float = 0
    exceeded_max: bool = False

    @field_validator("adjustment")
    @classmethod
    def validate_adjustment(cls, value) -> float:
        """
        For readability, limit float to only two decimals
        """
        return round(value, 2)


class Player(EmbeddedModel):
    """
    Store all player information, with an added method for
    returning a randomized point projection, based on historical distributions
    """

    name: str
    position: str
    position_tier: str = (
        ""  # DST & K do not have meaningful tiers, because they're very streamable
    )
    nfl_team: str  # Abbreviation, for current draft year
    points: Dict[str, PlayerPoints]  # Key is the year of the points
    drafted: bool = False

    # Optional cross-source consensus fields, populated when players are
    # synced from blended rankings; None/empty on the CSV upload path
    adp: Union[float, None] = None
    consensus_rank: Union[float, None] = None
    tier: Union[int, None] = None
    source_values: Dict[str, float] = {}

    @field_validator("position", "position_tier")
    @classmethod
    def validate_position(cls, value) -> str:
        """
        Ensure the position is lowercase
        """
        return value.lower()

    def draft_player(self):
        """
        Update the player's draft status to true (drafted)
        """
        self.drafted = True

    def randomized_points(
        self,
        distributions: PositionTierDistributions = PositionTierDistributions(),
        max_points: PositionMaxPoints = PositionMaxPoints(),
        max_points_adjustment: float = MAX_RANDOM_ADJUSTMENT,
        year: str = str(DRAFT_YEAR),
    ) -> PlayerPointsRandomized:
        """
        Return a random point projection for the player, if a distribution exists
        """
        output = {"projected_points": round(self.points[str(year)].projected_points)}

        # If the tier distribution is not available (DST & K), return the projected points
        tier_distribution = distributions.model_dump().get(self.position_tier, None)
        if not tier_distribution:
            output["randomized_points"] = output["projected_points"]

        # Randomly selection an adjustment from the distribution and apply it
        else:
            output["adjustment"] = random.choice(tier_distribution)
            output["randomized_points"] = round(
                self.points[str(year)].projected_points * (1.0 + output["adjustment"])
            )

            # If the adjustment exceeds the max points, return the max points
            max_allowed = int(
                max_points.model_dump()[self.position.lower()]
                * (1 + max_points_adjustment)
            )
            if output["randomized_points"] > max_allowed:
                output["randomized_points"] = max_allowed
                output["exceeded_max"] = True

            # If the adjustment is somehow negative, return zero points
            # Zero points simulates an early season-ending injury (like 2023 Aaron Rodgers)
            elif output["randomized_points"] < 0:
                output["randomized_points"] = 0

        # Return the output as a PlayerPointsRandomized object
        return PlayerPointsRandomized(**output)


class Players(EmbeddedModel):
    """
    Segment players into lists by position, and assign position tiers
    which are helpful for simulating randomness in draft outcomes
    """

    qb: List[Player] = []
    rb: List[Player] = []
    wr: List[Player] = []
    te: List[Player] = []
    dst: List[Player] = []
    k: List[Player] = []
    players: List[Player] = []
    years: List[str] = []
    ready_players: bool = False

    @model_validator(mode="before")
    def assign_players_to_positions(cls, data):
        """
        Assign the player to the correct position and position tiers
        """
        if "ready_players" in data and data["ready_players"]:
            return data

        # Ensure all players are Player objects
        positions = ["qb", "rb", "wr", "te", "dst", "k"]
        positions_and_players = positions + ["players"]
        for key in positions_and_players:
            if key in data and not all(
                [isinstance(player, Player) for player in data[key]]
            ):
                data[key] = [Player(**player) for player in data[key]]

        # Get the years that the players have on record
        data["years"] = []
        if "players" in data:
            years = set()
            for player in data["players"]:
                years.update(player.points.keys())
                if player.position in data:
                    data[player.position].append(player)
                else:
                    data[player.position] = [player]
            data["years"] = sorted(list(years))

        # Rank players within each season separately, so multi-season
        # historical files neither crash nor compete across seasons
        tiers = pt.model_dump()
        for year in data["years"]:
            for position in positions:
                if position not in data:
                    continue
                year_players = sorted(
                    [p for p in data[position] if year in p.points],
                    key=lambda x: x.points[year].projected_points,
                    reverse=True,
                )
                if position not in tiers:  # DST & K do not have tiers
                    for player in year_players:
                        player.position_tier = player.position
                else:
                    tier = tiers[position]
                    for i, player in enumerate(year_players):
                        if i < tier["1"]:
                            player.position_tier = f"{position}1"
                        elif i < tier["2"]:
                            player.position_tier = f"{position}2"
                        else:
                            player.position_tier = f"{position}3"

        # Keep each position list ordered by the player's most recent season
        for position in positions:
            if position in data:
                data[position] = sorted(
                    data[position],
                    key=lambda x: x.points[max(x.points)].projected_points,
                    reverse=True,
                )

        # Return the data
        data["ready_players"] = True
        return data
