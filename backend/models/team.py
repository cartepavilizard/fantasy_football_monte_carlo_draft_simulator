# -*- coding: utf-8 -*-
"""
ODMANTIC MODELS FOR TEAMS
"""
from .config import DRAFT_YEAR, ROUND_SIZE, SNAKE_DRAFT
import copy
import datetime
from .player import Player, Players
from .position import PositionMaxPoints, PositionSizes, PositionTierDistributions
from odmantic import EmbeddedModel, Model, ObjectId, Reference
from odmantic import Field as ODField
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sklearn.base import RegressorMixin
from typing import List


"""
TEAM HELPER FUNCTION
"""


def fill_starters(roster, sizes: PositionSizes):
    """
    Use projected points to fill the roster with the best players
    (used twice within the Team model, so it's a helper function)
    """
    not_flex = []
    output = {}

    # Perform the iteration for each position
    for position, size in sizes.model_dump().items():
        players = [player for player in roster if player["position"] == position]
        if players:

            # Sort by projected points and take the top players
            output[position] = sorted(
                players,
                key=lambda x: x["points"][str(DRAFT_YEAR)]["projected_points"],
                reverse=True,
            )[:size]

            # Add those players to the not_flex list
            not_flex.extend(output[position])

    # Take the best remaining RB, WR, or TE for flex
    flex_players = [
        player
        for player in roster
        if player["position"] in ["rb", "wr", "te"] and player not in not_flex
    ]
    output["flex"] = sorted(
        flex_players,
        key=lambda x: x["points"][str(DRAFT_YEAR)]["projected_points"],
        reverse=True,
    )[: sizes.flex]

    # Combine all positions for starters
    positions = sizes.model_dump().keys() | {"flex"}
    output["starters"] = [
        player
        for position in positions
        if position in output
        for player in output[position]
    ]
    return output


"""
MODELS
"""


class Team(EmbeddedModel):
    """
    A fantasy football team with a name, owner, and roster of players,
    which updates its starters with the best players when they are added
    to the roster
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    # Team information, including whether or not this team is the "simulator's"
    name: str
    owner: str
    simulator: bool = False
    draft_order: int

    # Starting-lineup sizes for the team's league (defaults to env-var sizes)
    position_sizes: PositionSizes = PositionSizes()

    # Roster is a list of players, while starters are the best for a position
    roster: List[Player] = []
    starters: List[Player] = []

    # Starting positions
    qb: List[Player] = []
    rb: List[Player] = []
    wr: List[Player] = []
    te: List[Player] = []
    flex: List[Player] = []
    dst: List[Player] = []
    k: List[Player] = []

    @model_validator(mode="before")
    def autofill_starters(cls, data):
        """
        Use the roster to autofill the positions for starters
        """
        if "roster" not in data or not data["roster"]:
            return data  # If roster is not in data, just return the data - there's nothing to do
        sizes = data.get("position_sizes") or PositionSizes()
        if isinstance(sizes, dict):
            sizes = PositionSizes(**sizes)
        starters = fill_starters(data["roster"], sizes)
        for position, players in starters.items():
            data[position] = players
        data["starters"] = starters.get("starters", [])
        return data

    def draft_turn_position_weights(
        self, pick_number: int, model: RegressorMixin
    ) -> dict:
        """
        Use the starting line-up to determine the weight each
        position should have when randomly selecting a player
        """
        position_weights = {}
        probabilities = model.predict_proba([[pick_number]])[0]
        for i, position in enumerate(model.classes_):
            position_weights[position.lower()] = probabilities[i]

        # For each position, check if the starters are filled
        starting_positions = ["qb", "rb", "wr", "te", "dst", "k"]
        sizes = self.position_sizes.model_dump()
        starting_filled = 0
        for position in starting_positions:
            if len(getattr(self, position)) == sizes[position]:
                starting_filled += 1

        # If all of the important positions are filled, return the position weights
        if starting_filled == len(starting_positions):
            return position_weights

        # Otherwise, adjust the weights based on the number of important positions filled
        for position in starting_positions:
            if len(getattr(self, position)) == sizes[position]:
                position_weights[position] = 0

        # Recalculate the total weight and return the position weights
        total_weight = sum(position_weights.values())
        if total_weight == 0:
            open_positions = [
                p
                for p in starting_positions
                if len(getattr(self, p)) < sizes[p]
            ]
            return {p: 1 / len(open_positions) for p in open_positions}
        return {
            position: weight / total_weight
            for position, weight in position_weights.items()
        }

    def projected_roster_points(self, year: int = DRAFT_YEAR) -> int:
        """
        Calculate the total projected points for the whole roster, not just the starters,
        using the draft year as the default year
        """
        return sum([player.points[year].projected_points for player in self.roster])

    def projected_starter_points(self, year: int = DRAFT_YEAR) -> int:
        """
        Calculate the total projected points for the starters only,
        using the draft year as the default year
        """
        return sum([player.points[year].projected_points for player in self.starters])

    def randomized_roster_points(
        self,
        distributions: PositionTierDistributions = PositionTierDistributions(),
        max_points: PositionMaxPoints = PositionMaxPoints(),
        year: int = DRAFT_YEAR,
    ) -> int:
        """
        Calculate the total randomized points for the whole roster, not just the starters,
        using the draft year as the default year
        """
        return sum(
            [
                player.randomized_points(
                    distributions=distributions, max_points=max_points, year=year
                ).randomized_points
                for player in self.roster
            ]
        )

    def randomized_starter_points(
        self,
        distributions: PositionTierDistributions = PositionTierDistributions(),
        max_points: PositionMaxPoints = PositionMaxPoints(),
        year: int = DRAFT_YEAR,
    ) -> int:
        """
        Calculate the total randomized points for the starters only,
        using the draft year as the default year
        """
        roster_copy = copy.deepcopy(self.roster)
        for i, player in enumerate(roster_copy):
            player_data = player.model_dump()
            player_data["points"][str(year)]["projected_points"] = (
                player.randomized_points(
                    distributions=distributions, max_points=max_points, year=year
                ).randomized_points
            )
            new_player = Player(**player_data)
            roster_copy[i] = new_player
        starters = fill_starters(
            [x.model_dump() for x in roster_copy], self.position_sizes
        )["starters"]
        return sum(
            [player["points"][str(year)]["projected_points"] for player in starters]
        )


class LogisticRegressionVariables(EmbeddedModel):
    """
    Variables for the logistic regression model, which are stored in the league
    """

    x: List[int] = []
    y: List[str] = []


class LeagueSimple(BaseModel):
    """
    Just the basic information about a league, as a Pydantic model,
    to return to the user in the API, not model in the database
    """

    created: datetime.datetime = Field(default_factory=datetime.datetime.now)
    name: str = ""
    ready_for_draft: bool
    copy_for_draft: bool
    id: ObjectId


class League(Model):
    """
    All teams in the league, with draft order, based on settings
    """

    created: datetime.datetime = ODField(default_factory=datetime.datetime.now)
    name: str = ""
    roster_size: int = 14
    position_sizes: PositionSizes = PositionSizes()
    round_size: int = 14
    ready_for_draft: bool = False
    copy_for_draft: bool = (
        False  # If a league is a copy, it can go in drafts and is editable
    )
    teams: List[Team]
    snake_draft: bool = SNAKE_DRAFT
    draft_order: List[int] = []
    draft_results: List[Team] = []
    current_draft_turn: int = 0
    players: Players = Players()
    position_tier_distributions: PositionTierDistributions = PositionTierDistributions()
    ready_position_tier_distributions: bool = False
    position_max_points: PositionMaxPoints = PositionMaxPoints()
    ready_position_max_points: bool = False
    logistic_regression_variables: LogisticRegressionVariables = (
        LogisticRegressionVariables()
    )

    @model_validator(mode="before")
    def sort_by_draft_order_and_validate_ready_to_draft(cls, data):
        """
        Sort the teams into their draft order and then create a list of their indices
        to help populate the draft results and validate whether a league is ready to draft
        """
        data.setdefault("teams", [])
        data.setdefault("current_draft_turn", 0)
        data.setdefault("snake_draft", SNAKE_DRAFT)
        if not all([isinstance(team, Team) for team in data["teams"]]):
            data["teams"] = [Team(**team) for team in data["teams"]]
        if "logistic_regression_variables" in data and not (
            isinstance(
                data["logistic_regression_variables"], LogisticRegressionVariables
            )
        ):
            data["logistic_regression_variables"] = LogisticRegressionVariables(
                **data["logistic_regression_variables"]
            )
        if "players" in data and not isinstance(data["players"], Players):
            data["players"] = Players(**data["players"])

        # Sort the teams
        data["teams"] = sorted(data["teams"], key=lambda x: x.draft_order)

        # For the number of rounds, create the draft order as a list
        data["draft_order"] = []
        team_indices = [data["teams"].index(team) for team in data["teams"]]
        rounds = int(data.get("round_size", ROUND_SIZE))
        for i in range(rounds):
            if data["snake_draft"]:
                if i % 2 == 0:
                    data["draft_order"].extend(team_indices)
                else:
                    data["draft_order"].extend(team_indices[::-1])
            else:
                data["draft_order"].extend(team_indices)

        # For whichever current_draft_turn we are on, pop the first team from the draft order
        for _ in range(data["current_draft_turn"]):
            data["draft_order"].pop(0)

        # Check if we are ready to draft
        if (
            "teams" in data
            and len(data["teams"]) > 0
            and "players" in data
            and len(data["players"].players) > 0
            and "logistic_regression_variables" in data
            and len(data["logistic_regression_variables"].x) > 0
            and len(data["logistic_regression_variables"].y) > 0
            and "ready_position_tier_distributions" in data
            and data["ready_position_tier_distributions"]
            and "ready_position_max_points" in data
            and data["ready_position_max_points"]
        ):
            data["ready_for_draft"] = True

        # Return the data to populate the model
        return data

    def rank_by_projected_team_points(self) -> list:
        """
        Return a sorted list of the teams by projected points
        """
        return sorted(
            self.teams, key=lambda x: x.projected_roster_points(), reverse=True
        )

    def add_player_to_current_draft_turn_team(self, player: Player) -> Team:
        """
        Draft a player and return the team that drafted the player
        """
        team_index = self.draft_order[0]
        team = self.teams[team_index]

        # Append the player to the team's roster
        team.roster.append(player)

        # Reinitialize team, appended to draft_results, with the new player on the roster
        team_dict = team.model_dump()
        self.teams[team_index] = Team(**team_dict)
        self.draft_results.append(Team(**team_dict))

        # Increment the current draft turn
        self.current_draft_turn += 1
        return


class DraftSimple(BaseModel):
    """
    Just the basic information about a draft, as a Pydantic model,
    to return to the user in the API, not model in the database
    """

    created: datetime.datetime = Field(default_factory=datetime.datetime.now)
    id: ObjectId


class Draft(Model):
    """
    Drafts include a copy of the league, which has all necessary information,
    and a created date
    """

    league: League = Reference()
    created: datetime.datetime = ODField(default_factory=datetime.datetime.now)


class MonteCarloSimulationResult(BaseModel):
    """
    Pydantic model for the Monte Carlo simulation results
    """

    qb: float = 0
    rb: float = 0
    wr: float = 0
    te: float = 0
    dst: float = 0
    k: float = 0
    iterations: int = 0
