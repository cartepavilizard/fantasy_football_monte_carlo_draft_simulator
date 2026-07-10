# -*- coding: utf-8 -*-
"""
Phase 4 engine integration: stage-1 position blending, stage-2
reach-aware player sampling, sample-size gating, the USE_OWNER_PROFILES
flag (off = identical to the pre-profile engine), and the owner-mapping
endpoint that materializes profiles onto a league.
"""
import asyncio
import random
from types import SimpleNamespace

import pytest
from pytest import approx

from models import config as app_config
from models.sources import OwnerProfile
from models.team import Team
from models.tendencies import (
    REACH_SD_REFERENCE,
    blend_position_weights,
    build_generic_tendencies,
    candidate_weights,
    profile_weight,
    reach_sd_for,
)

# --- pure math ----------------------------------------------------------------


def test_profile_weight_gates_on_sample_size():
    assert profile_weight(0) == 0.0
    assert profile_weight(4) == 0.0  # below the floor: contributes nothing
    assert profile_weight(10) == approx(0.7 * 10 / 25)
    assert profile_weight(25) == approx(0.7)
    assert profile_weight(500) == approx(0.7)  # capped


RB_HEAVY = {
    "position_frequency": {"1-2": {"n": 25, "shares": {"rb": 1.0}}},
    "reach": {"n": 30, "mean_delta": -2.0, "sd_delta": 12.0},
    "post_miss": {"n": 25, "shift": 0.5},
}


def test_blend_moves_toward_owner_frequencies():
    model = {"rb": 0.2, "wr": 0.8}
    blended = blend_position_weights(model, RB_HEAVY, round_num=1)
    # w=0.7: rb = 0.7*1.0 + 0.3*0.2 = 0.76
    assert blended["rb"] == approx(0.76)
    assert blended["wr"] == approx(0.24)


def test_thin_sample_leaves_model_untouched():
    thin = {"position_frequency": {"1-2": {"n": 3, "shares": {"rb": 1.0}}}}
    model = {"rb": 0.2, "wr": 0.8}
    assert blend_position_weights(model, thin, round_num=1) == approx(model)
    # wrong bucket also leaves it untouched
    assert blend_position_weights(model, RB_HEAVY, round_num=8) == approx(model)


def test_post_miss_shift_applies_and_renormalizes():
    model = {"rb": 0.5, "wr": 0.5}
    tendencies = {"post_miss": {"n": 25, "shift": 0.5}}
    blended = blend_position_weights(
        model, tendencies, round_num=8, missed_position="wr"
    )
    # wr = 0.5 + 0.5*0.7 = 0.85, then normalized over 1.35
    assert blended["wr"] == approx(0.85 / 1.35)
    assert sum(blended.values()) == approx(1.0)


def test_candidate_weights_favor_top_and_flatten_with_reach_sd():
    adps = [None] * 4
    chalky = candidate_weights(adps, 10, reach_sd=2.0)  # temp clipped to min
    loose = candidate_weights(adps, 10, reach_sd=20.0)  # temp clipped to max
    assert chalky[0] > chalky[1] > chalky[2]
    # A chalky owner concentrates far more mass on the top candidate
    assert chalky[0] / sum(chalky) > loose[0] / sum(loose)


def test_candidate_weights_blend_projection_and_adp_order():
    # candidate 1 is worse by projection but the market's clear favorite
    weights = candidate_weights([50.0, 5.0], 10, reach_sd=6.0)
    assert weights[0] == approx(weights[1])  # ranks average out to a tie


def test_generic_tendencies_pool_by_sample_size():
    generic = build_generic_tendencies(
        [
            {"reach": {"n": 10, "sd_delta": 4.0, "mean_delta": 0.0}},
            {"reach": {"n": 30, "sd_delta": 8.0, "mean_delta": -2.0}},
            {"reach": {"n": 0}},
        ]
    )
    assert generic["reach_sd"] == approx((10 * 4 + 30 * 8) / 40)
    assert generic["n"] == 40
    assert build_generic_tendencies([]) == {}


def test_reach_sd_prefers_owner_then_generic_then_reference():
    assert reach_sd_for(RB_HEAVY, {"reach_sd": 5.0}) == 12.0
    assert reach_sd_for({"reach": {"n": 2, "sd_delta": 12.0}}, {"reach_sd": 5.0}) == 5.0
    assert reach_sd_for({}, {}) == REACH_SD_REFERENCE


# --- stage 1 through the Team model ---------------------------------------------


class FakeModel:
    classes_ = ["RB", "WR"]

    def predict_proba(self, x):
        return [[0.2, 0.8]]


def make_team(**kwargs):
    return Team(name="T", owner="Dave", draft_order=1, **kwargs)


def test_team_blends_when_profiled():
    team = make_team(owner_tendencies=RB_HEAVY)
    weights = team.draft_turn_position_weights(1, FakeModel(), round_num=1)
    assert weights["rb"] == approx(0.76)


def test_team_without_profile_matches_model():
    weights = make_team().draft_turn_position_weights(1, FakeModel(), round_num=1)
    assert weights == approx({"rb": 0.2, "wr": 0.8})


def test_flag_off_ignores_tendencies(monkeypatch):
    monkeypatch.setattr(app_config, "USE_OWNER_PROFILES", False)
    team = make_team(owner_tendencies=RB_HEAVY)
    weights = team.draft_turn_position_weights(1, FakeModel(), round_num=1)
    assert weights == approx({"rb": 0.2, "wr": 0.8})


# --- stage 2 in simulate_pick's player choice ------------------------------------


def stub_player(name, adp=None):
    return SimpleNamespace(name=name, adp=adp, position="rb")


def choose(app_module, players, team_tendencies, generic, seed=1):
    league = SimpleNamespace(generic_tendencies=generic)
    team = SimpleNamespace(owner_tendencies=team_tendencies)
    random.seed(seed)
    return app_module._choose_position_player(players, 10, team, league)


def test_stage2_is_deterministic_best_when_inactive(app_module, monkeypatch):
    players = [stub_player(f"P{i}") for i in range(5)]
    # No tendencies anywhere -> old behavior even with the flag on
    assert choose(app_module, players, {}, {}).name == "P0"
    # Flag off -> old behavior even when mapped
    monkeypatch.setattr(app_config, "USE_OWNER_PROFILES", False)
    assert choose(app_module, players, RB_HEAVY, {"reach_sd": 8.0}).name == "P0"


def test_stage2_samples_but_still_prefers_the_top(app_module):
    players = [stub_player(f"P{i}", adp=float(i + 1)) for i in range(8)]
    counts = {}
    for seed in range(300):
        name = choose(
            app_module, players, {}, {"reach_sd": 8.0}, seed=seed
        ).name
        counts[name] = counts.get(name, 0) + 1
    assert counts["P0"] == max(counts.values())  # top candidate still favored
    assert len(counts) > 1  # but no longer deterministic


def test_stage2_chalky_owner_concentrates_more_than_reacher(app_module):
    players = [stub_player(f"P{i}", adp=float(i + 1)) for i in range(8)]
    chalky_tendencies = {"reach": {"n": 30, "sd_delta": 1.0}}
    loose_tendencies = {"reach": {"n": 30, "sd_delta": 30.0}}

    def top_share(tendencies):
        top = 0
        for seed in range(300):
            if choose(app_module, players, tendencies, {}, seed=seed).name == "P0":
                top += 1
        return top / 300

    assert top_share(chalky_tendencies) > top_share(loose_tendencies)


# --- owner mapping endpoint -------------------------------------------------------


def profile(key, names, n=25):
    return OwnerProfile(
        profile_key=key,
        display_names=names,
        member_guids=[key],
        total_picks_observed=n,
        metrics={
            "position_frequency": {"1-2": {"n": n, "shares": {"rb": 0.8, "wr": 0.2}}},
            "reach": {"n": n, "mean_delta": -1.0, "sd_delta": 7.0},
            "post_miss": {"n": n, "shift": 0.2},
        },
    )


def save_profiles(app_module, profiles):
    async def go():
        await app_module.engine.save_all(profiles)

    asyncio.run(go())


def test_mapping_requires_profiles(client, league_id):
    response = client.post(f"/league/{league_id}/owners/map")
    assert response.status_code == 400
    assert "ingest" in response.json()["detail"]


def test_auto_mapping_matches_names_and_flags_the_rest(
    client, app_module, league_id
):
    save_profiles(
        app_module,
        [profile("{G-J}", ["Julia"]), profile("{G-L}", ["Laura"])],
    )
    response = client.post(f"/league/{league_id}/owners/map")
    assert response.status_code == 200, response.text
    report = response.json()
    matched_keys = set(report["matched"].values())
    assert matched_keys == {"{G-J}", "{G-L}"}
    assert len(report["unmatched"]) == 12  # 14 teams, 2 matched
    assert report["generic_tendencies"]["reach_sd"] == approx(7.0)

    league = client.get(f"/league/{league_id}").json()
    mapped = [t for t in league["teams"] if t["owner_profile_key"]]
    assert len(mapped) == 2
    assert mapped[0]["owner_tendencies"]["reach"]["n"] == 25
    assert league["generic_tendencies"]["n"] == 50


def test_ambiguous_names_stay_unmatched_until_manual(client, app_module, league_id):
    save_profiles(
        app_module,
        [profile("{G-1}", ["Jake"]), profile("{G-2}", ["Jake"])],
    )
    report = client.post(f"/league/{league_id}/owners/map").json()
    assert report["matched"] == {}  # ambiguous "Jake" never auto-assigned

    manual = client.post(
        f"/league/{league_id}/owners/map?team_name=Team 3&profile_key={{G-1}}"
    )
    assert manual.status_code == 200
    assert manual.json()["matched"] == {"Team 3": "{G-1}"}


def test_manual_mapping_validates_inputs(client, app_module, league_id):
    save_profiles(app_module, [profile("{G-1}", ["Someone"])])
    assert (
        client.post(f"/league/{league_id}/owners/map?team_name=Team 3").status_code
        == 400
    )
    assert (
        client.post(
            f"/league/{league_id}/owners/map?team_name=Nope&profile_key={{G-1}}"
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/league/{league_id}/owners/map?team_name=Team 3&profile_key=missing"
        ).status_code
        == 404
    )
