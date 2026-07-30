# -*- coding: utf-8 -*-
"""
Per-league tendency scoping (the load-bearing reason the ESPN league id
is a REQUIRED positional argument to build_team_tendencies /
build_generic_tendencies).

Round buckets are not comparable across league sizes: round 3 is overall
picks 25-36 in a 12-team league but 21-30 in a 10-team league, so feeding
a 12-team-blended tendency into a 10-team simulation misstates what an
owner does at that point on the board. The fix is that the engine always
reads a per-league block and can never silently fall back to the merged
top-level metrics. These tests pin that contract end to end.
"""
import pytest
from pytest import approx

from models.team import League
from models.tendencies import (
    MIN_SAMPLE,
    blend_position_weights,
    build_generic_tendencies,
    build_team_tendencies,
    profile_weight,
)


# --- the same owner resolves to different tendencies in two leagues ------------


def _metrics(twelve=None, ten=None):
    """An OwnerProfile.metrics dict with a top-level (merged) view and two
    per-league blocks. The top-level view is intentionally DIFFERENT from
    each league block so any silent fallback is detectable."""
    return {
        "position_frequency": {"1-2": {"n": 50, "shares": {"rb": 1.0}}},
        "reach": {"n": 50, "mean_delta": -9.0, "sd_delta": 99.0},
        "post_miss": {"n": 50, "shift": 0.9},
        "by_league": {"12": twelve or {}, "10": ten or {}},
    }


def test_same_owner_resolves_differently_across_leagues():
    twelve = {
        "position_frequency": {"1-2": {"n": 25, "shares": {"rb": 1.0}}},
        "reach": {"n": 25, "mean_delta": -2.0, "sd_delta": 7.0},
        "post_miss": {"n": 25, "shift": 0.2},
    }
    ten = {
        "position_frequency": {"1-2": {"n": 25, "shares": {"wr": 1.0}}},
        "reach": {"n": 25, "mean_delta": -5.0, "sd_delta": 11.0},
        "post_miss": {"n": 25, "shift": 0.4},
    }
    metrics = _metrics(twelve=twelve, ten=ten)

    in_twelve = build_team_tendencies(metrics, "owner", 12)
    in_ten = build_team_tendencies(metrics, "owner", 10)

    assert in_twelve["position_frequency"]["1-2"]["shares"] == {"rb": 1.0}
    assert in_ten["position_frequency"]["1-2"]["shares"] == {"wr": 1.0}
    assert in_twelve["reach"]["sd_delta"] == 7.0
    assert in_ten["reach"]["sd_delta"] == 11.0
    assert in_twelve["post_miss"]["shift"] == 0.2
    assert in_ten["post_miss"]["shift"] == 0.4

    # The merged top-level numbers must NOT leak through under either league:
    # a silent fallback to the merged block would surface sd 99.0 / shift 0.9.
    assert in_twelve["reach"]["sd_delta"] != 99.0
    assert in_ten["reach"]["sd_delta"] != 99.0
    assert in_twelve["post_miss"]["shift"] != 0.9
    assert in_ten["post_miss"]["shift"] != 0.9

    # str and int league ids resolve identically.
    assert build_team_tendencies(metrics, "owner", "12") == in_twelve
    assert build_team_tendencies(metrics, "owner", "10") == in_ten


# --- an unknown league returns empty -> zero profile weight -> model untouched


def test_unknown_league_produces_zero_weight_and_leaves_model_untouched():
    # Owner has data only in league 12; asking for league 7 returns empty.
    metrics = _metrics(
        twelve={
            "position_frequency": {"1-2": {"n": 25, "shares": {"rb": 1.0}}},
            "reach": {"n": 25, "sd_delta": 7.0},
            "post_miss": {"n": 25, "shift": 0.2},
        }
    )
    tendencies = build_team_tendencies(metrics, "owner", 7)
    assert tendencies == {
        "profile_key": "owner",
        "position_frequency": {},
        "reach": {},
        "post_miss": {},
    }
    # Empty block -> bucket n=0 -> profile_weight(0) == 0.0
    bucket = tendencies["position_frequency"].get("1-2", {})
    assert profile_weight(bucket.get("n", 0)) == 0.0

    model = {"rb": 0.2, "wr": 0.8}
    blended = blend_position_weights(model, tendencies, round_num=1)
    assert blended == approx(model)  # 'augments, not replaces'


# --- a missed call site is an immediate TypeError, not a silent merge


def test_build_team_tendencies_requires_league_id():
    with pytest.raises(TypeError):
        build_team_tendencies({"by_league": {}}, "owner")  # type: ignore[call-arg]


def test_build_generic_tendencies_requires_league_id():
    with pytest.raises(TypeError):
        build_generic_tendencies([{"by_league": {}}])  # type: ignore[call-arg]


# --- generic pooling differs between two leagues --------------------------------


def test_generic_pooling_differs_between_two_leagues():
    # Two owners; each has reach data in BOTH leagues, with DIFFERENT spreads.
    def owner(twelve_sd, ten_sd, n=20):
        return {
            "by_league": {
                "12": {"reach": {"n": n, "sd_delta": twelve_sd, "mean_delta": 0.0}},
                "10": {"reach": {"n": n, "sd_delta": ten_sd, "mean_delta": 0.0}},
            }
        }

    metrics_list = [owner(4.0, 9.0), owner(6.0, 11.0)]

    generic_twelve = build_generic_tendencies(metrics_list, 12)
    generic_ten = build_generic_tendencies(metrics_list, 10)

    assert generic_twelve["reach_sd"] == approx((4.0 + 6.0) / 2)
    assert generic_ten["reach_sd"] == approx((9.0 + 11.0) / 2)
    assert generic_twelve["reach_sd"] != generic_ten["reach_sd"]
    assert generic_twelve["n"] == generic_ten["n"] == 40

    # Owners with no block for the requested league contribute nothing,
    # so pooling only league-10 owners for league 10 is correct.
    only_twelve = owner(4.0, ten_sd=None)
    only_twelve["by_league"]["10"] = {}  # no ten data for this owner
    mixed = [owner(4.0, 9.0), only_twelve]
    assert build_generic_tendencies(mixed, 10)["reach_sd"] == approx(9.0)
    assert build_generic_tendencies(mixed, 12)["reach_sd"] == approx(4.0)
    # pool with nobody having the league -> {}
    assert build_generic_tendencies(mixed, 999) == {}


# --- League.espn_league_id defaults to None -------------------------------------


def test_league_espn_league_id_defaults_to_none():
    league = League(
        teams=[{"name": "T", "owner": "Dave", "draft_order": 1}]
    )
    assert league.espn_league_id is None


# --- the mapping endpoint 400s without a stored or passed espn_league_id --------


async def _save_profile(app_module, league, key, names, n=25):
    from models.sources import OwnerProfile

    block = {
        "position_frequency": {"1-2": {"n": n, "shares": {"rb": 0.8, "wr": 0.2}}},
        "reach": {"n": n, "mean_delta": -1.0, "sd_delta": 7.0},
        "post_miss": {"n": n, "shift": 0.2},
    }
    await app_module.engine.save_all(
        [
            OwnerProfile(
                profile_key=key,
                display_names=names,
                member_guids=[key],
                total_picks_observed=n,
                metrics={"by_league": {str(league): block}},
            )
        ]
    )


def test_mapping_400s_without_stored_or_passed_league(client, app_module, league_id):
    import asyncio

    asyncio.run(_save_profile(app_module, 111, "{G-J}", ["Julia"]))
    response = client.post(f"/league/{league_id}/owners/map")  # no espn_league_id
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "ESPN league" in detail
    assert "espn_league_id" in detail.lower()
