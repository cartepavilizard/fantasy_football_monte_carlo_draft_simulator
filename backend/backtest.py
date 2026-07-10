# -*- coding: utf-8 -*-
"""
OWNER-PROFILE BACKTEST (the Phase 4 ship gate)

Leave-one-season-out replay of every ingested draft. For each held-out
(league, season), both engines predict every real pick:

- generic arm: the logistic regression P(position | pick number),
  trained on the league's OTHER seasons — today's opponent model
- profile arm: the same probabilities blended with the picking owner's
  tendency profile, built from all picks EXCLUDING the held-out season

Scored on position hit rate and player-in-top-K rate (top K remaining
players by that season's historical ADP at the predicted position).
Roster-need zeroing is omitted from BOTH arms (historical roster rules
are unknowable), so the comparison stays fair.
"""
from typing import Dict, List, Optional

from sklearn.linear_model import LogisticRegression

from models.tendencies import (
    MISS_ADP_AFTER,
    MISS_ADP_BEFORE,
    MISS_LOOKBACK,
    blend_position_weights,
    build_team_tendencies,
)
from profiling import extract_profiles

MIN_TRAINING_PICKS = 10


def _argmax(weights: Dict[str, float]) -> Optional[str]:
    if not weights:
        return None
    return max(weights, key=weights.get)


def _missed_position(board_so_far: list, pick_number: int) -> Optional[str]:
    """Mirror of the engine's inferred-miss detection, on historical rows"""
    best_position = None
    best_distance = None
    for prior in board_so_far[-MISS_LOOKBACK:]:
        if prior.historical_adp is None or not prior.position:
            continue
        if (
            pick_number - MISS_ADP_BEFORE
            <= prior.historical_adp
            <= pick_number + MISS_ADP_AFTER
        ):
            distance = abs(prior.historical_adp - pick_number)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_position = prior.position
    return best_position


def _top_k_names(remaining: dict, position: str, top_k: int) -> set:
    candidates = sorted(
        (
            (adp, name)
            for name, (adp, pos) in remaining.items()
            if pos == position
        ),
    )
    return {name for _, name in candidates[:top_k]}


class _Arm:
    def __init__(self):
        self.position_hits = 0
        self.position_n = 0
        self.topk_hits = 0
        self.topk_n = 0

    def report(self, top_k: int) -> dict:
        return {
            "position_hit_rate": (
                round(self.position_hits / self.position_n, 4)
                if self.position_n
                else None
            ),
            "position_n": self.position_n,
            f"player_top{top_k}_rate": (
                round(self.topk_hits / self.topk_n, 4) if self.topk_n else None
            ),
            f"player_top{top_k}_n": self.topk_n,
        }


def evaluate(picks: list, alias_map: Optional[dict] = None, top_k: int = 5) -> dict:
    """Pure & sync: HistoricalPick rows -> generic vs profile comparison"""
    alias_map = alias_map or {}
    seasons: Dict[tuple, list] = {}
    for pick in picks:
        seasons.setdefault((pick.espn_league_id, pick.season), []).append(pick)

    generic, profile = _Arm(), _Arm()
    evaluated, skipped = [], []

    for (league_id, season), season_picks in sorted(seasons.items()):
        if any(pick.bid_amount for pick in season_picks):
            skipped.append({"league": league_id, "season": season, "why": "auction"})
            continue

        training = [
            pick
            for pick in picks
            if pick.espn_league_id == league_id
            and pick.season != season
            and pick.position
            and not pick.is_keeper
        ]
        if (
            len(training) < MIN_TRAINING_PICKS
            or len({pick.position for pick in training}) < 2
        ):
            skipped.append(
                {"league": league_id, "season": season, "why": "thin training data"}
            )
            continue
        model = LogisticRegression(max_iter=1000)
        model.fit(
            [[pick.overall_pick] for pick in training],
            [pick.position for pick in training],
        )

        # Profiles from everything except the held-out league-season
        held_out_ids = {id(pick) for pick in season_picks}
        profile_training = [pick for pick in picks if id(pick) not in held_out_ids]
        tendencies_by_key = {
            owner.profile_key: build_team_tendencies(
                owner.metrics, owner.profile_key
            )
            for owner in extract_profiles(profile_training, alias_map=alias_map)
        }

        board = sorted(season_picks, key=lambda pick: pick.overall_pick)
        remaining = {
            pick.raw_player_name: (pick.historical_adp, pick.position)
            for pick in board
            if pick.historical_adp is not None and pick.position
        }
        for index, pick in enumerate(board):
            if pick.is_keeper or not pick.position or not pick.member_guid:
                remaining.pop(pick.raw_player_name, None)
                continue

            probabilities = model.predict_proba([[pick.overall_pick]])[0]
            model_weights = {
                position.lower(): probability
                for position, probability in zip(model.classes_, probabilities)
            }
            generic_position = _argmax(model_weights)

            tendencies = tendencies_by_key.get(
                alias_map.get(pick.member_guid, pick.member_guid)
            )
            if tendencies:
                missed = _missed_position(board[:index], pick.overall_pick)
                profile_position = _argmax(
                    blend_position_weights(
                        model_weights,
                        tendencies,
                        pick.round_num,
                        missed_position=missed,
                    )
                )
            else:
                profile_position = generic_position

            for arm, predicted in [
                (generic, generic_position),
                (profile, profile_position),
            ]:
                arm.position_n += 1
                arm.position_hits += int(predicted == pick.position)
                if pick.historical_adp is not None:
                    arm.topk_n += 1
                    arm.topk_hits += int(
                        pick.raw_player_name
                        in _top_k_names(remaining, predicted, top_k)
                    )

            remaining.pop(pick.raw_player_name, None)
        evaluated.append({"league": league_id, "season": season})

    generic_report = generic.report(top_k)
    profile_report = profile.report(top_k)
    improvement = None
    if generic_report["position_hit_rate"] is not None:
        improvement = round(
            profile_report["position_hit_rate"]
            - generic_report["position_hit_rate"],
            4,
        )
    return {
        "seasons_evaluated": evaluated,
        "seasons_skipped": skipped,
        "top_k": top_k,
        "generic": generic_report,
        "profile": profile_report,
        "position_hit_rate_improvement": improvement,
    }
