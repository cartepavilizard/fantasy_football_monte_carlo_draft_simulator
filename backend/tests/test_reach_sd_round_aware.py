# -*- coding: utf-8 -*-
"""
Round-aware reach SD: the spread of (overall_pick - ADP) is wildly
round-dependent, so a single owner-wide number saturates
TEMPERATURE_MAX and lets late-round randomness leak into round 1.
These tests pin the end-to-end change: per-bucket spread in
_reach_stats, round-keyed selection in reach_sd_for, the under-sampled
fallbacks, and the downstream effect on candidate_weights.
"""
from pytest import approx

from models.tendencies import (
    REACH_SD_REFERENCE,
    ROUND_BUCKETS,
    bucket_for_round,
    build_generic_tendencies,
    candidate_weights,
    reach_sd_for,
)
from profiling import RECENCY_DECAY, _reach_stats

CURRENT = 2024


def weight_of(event) -> float:
    return RECENCY_DECAY ** (CURRENT - event["season"])


def event(delta, bucket, season=CURRENT, position="rb", overall=1, round_num=1):
    return {
        "season": season,
        "bucket": bucket,
        "adp_delta": delta,
        "position": position,
        "overall_pick": overall,
        "round_num": round_num,
    }


# --- _reach_stats per-bucket spread --------------------------------------------


def test_reach_stats_emits_per_bucket_spreads():
    # tight spread in the "1-2" bucket, wide in "10+"
    tight = [event(0, "1-2") for _ in range(5)]
    wide = [event(d, "10+") for d in (-30, -10, 0, 10, 30)]
    stats = _reach_stats(tight + wide, weight_of)

    # all the existing keys are still present
    for key in ("n", "mean_delta", "sd_delta", "reach_rate", "threshold_picks"):
        assert key in stats

    by_bucket = stats["by_bucket"]
    assert set(by_bucket) == {"1-2", "10+"}  # only buckets with events
    assert by_bucket["1-2"]["n"] == 5
    assert by_bucket["10+"]["n"] == 5

    # tight bucket sd is 0, wide bucket sd is sqrt(2000/5) = 20.0
    assert by_bucket["1-2"]["sd_delta"] == approx(0.0)
    assert by_bucket["10+"]["sd_delta"] == approx(20.0)
    # numerically right AND in the right direction
    assert by_bucket["1-2"]["sd_delta"] < by_bucket["10+"]["sd_delta"]


def test_reach_stats_only_emits_buckets_with_events():
    stats = _reach_stats([event(-2, "3-5"), event(3, "3-5")], weight_of)
    assert list(stats["by_bucket"]) == ["3-5"]


# --- reach_sd_for round-keyed selection -----------------------------------------


ROUND_AWARE_OWNER = {
    "reach": {
        "n": 60,
        "mean_delta": -8.0,
        "sd_delta": 23.62,  # overall figure (the old single number)
        "by_bucket": {
            "1-2": {"n": 10, "mean_delta": -1.0, "sd_delta": 5.13},
            "3-5": {"n": 12, "mean_delta": -6.0, "sd_delta": 18.86},
            "6-9": {"n": 14, "mean_delta": -9.0, "sd_delta": 23.24},
            "10+": {"n": 24, "mean_delta": -12.0, "sd_delta": 28.88},
        },
    }
}


def test_reach_sd_for_selects_the_right_bucket_for_a_round():
    generic = {"reach_sd": 30.0}
    assert reach_sd_for(ROUND_AWARE_OWNER, generic, round_num=1) == approx(5.13)
    assert reach_sd_for(ROUND_AWARE_OWNER, generic, round_num=2) == approx(5.13)
    assert reach_sd_for(ROUND_AWARE_OWNER, generic, round_num=4) == approx(18.86)
    assert reach_sd_for(ROUND_AWARE_OWNER, generic, round_num=7) == approx(23.24)
    assert reach_sd_for(ROUND_AWARE_OWNER, generic, round_num=12) == approx(28.88)


def test_under_sampled_own_bucket_falls_back_to_generic_bucket():
    # the swap: a thin own bucket now resolves to the generic bucket for
    # that round rather than the owner's round-blind lifetime average
    thin_own = {
        "reach": {
            "n": 40,
            "sd_delta": 16.12,  # the old lifetime fallback (too wide for rd 1)
            "by_bucket": {
                "1-2": {"n": 2, "sd_delta": 5.0},  # n < MIN_SAMPLE -> ignored
                "10+": {"n": 38, "sd_delta": 28.88},
            },
        }
    }
    generic = {
        "reach_sd": 23.62,
        "by_bucket": {
            "1-2": {"n": 10, "sd_delta": 5.13},
            "10+": {"n": 24, "sd_delta": 28.88},
        },
    }
    # round 1: own bucket thin -> generic 1-2 bucket wins (not the 16.12 lifetime)
    assert reach_sd_for(thin_own, generic, round_num=1) == approx(5.13)
    # round 12: own bucket is well-sampled -> still wins over the generic
    assert reach_sd_for(thin_own, generic, round_num=12) == approx(28.88)


def test_well_sampled_own_bucket_still_wins_over_generic_bucket():
    # even when the generic bucket is well-sampled, the owner's own bucket
    # for that round is the first choice
    owner = {
        "reach": {
            "n": 60,
            "sd_delta": 23.62,
            "by_bucket": {
                "1-2": {"n": 10, "sd_delta": 4.20},  # owner's own figure
            },
        }
    }
    generic = {
        "reach_sd": 23.62,
        "by_bucket": {
            "1-2": {"n": 50, "sd_delta": 5.13},  # well-sampled but generic
        },
    }
    assert reach_sd_for(owner, generic, round_num=1) == approx(4.20)


def test_no_generic_bucket_falls_through_to_owner_overall():
    # the old rung is still there: when no generic bucket exists for the
    # round, a thin own bucket falls through to the owner's overall figure
    thin_own = {
        "reach": {
            "n": 40,
            "sd_delta": 16.12,
            "by_bucket": {
                "1-2": {"n": 2, "sd_delta": 5.0},  # thin
            },
        }
    }
    # generic has no by_bucket at all -> owner overall is the next rung
    assert reach_sd_for(thin_own, {"reach_sd": 30.0}, round_num=1) == approx(16.12)
    # a thin generic bucket for the round also falls through to owner overall
    thin_generic = {
        "reach_sd": 30.0,
        "by_bucket": {"1-2": {"n": 2, "sd_delta": 5.0}},  # n < MIN_SAMPLE
    }
    assert reach_sd_for(thin_own, thin_generic, round_num=1) == approx(16.12)


def test_two_argument_behavior_is_unchanged():
    rb_heavy = {"reach": {"n": 30, "mean_delta": -2.0, "sd_delta": 12.0}}
    assert reach_sd_for(rb_heavy, {"reach_sd": 5.0}) == 12.0
    assert reach_sd_for(
        {"reach": {"n": 2, "sd_delta": 12.0}}, {"reach_sd": 5.0}
    ) == 5.0
    assert reach_sd_for({}, {}) == REACH_SD_REFERENCE
    # passing round_num=None is identical to omitting it
    assert reach_sd_for(rb_heavy, {"reach_sd": 5.0}, round_num=None) == 12.0


def test_generic_per_bucket_fallback_for_unprofiled_owner():
    generic = {
        "reach_sd": 23.62,
        "by_bucket": {
            "1-2": {"n": 10, "sd_delta": 5.13},
            "10+": {"n": 24, "sd_delta": 28.88},
        },
    }
    # owner has no profile at all
    assert reach_sd_for({}, generic, round_num=1) == approx(5.13)
    assert reach_sd_for({}, generic, round_num=12) == approx(28.88)
    # under-sampled generic bucket -> generic overall
    thin_generic = {
        "reach_sd": 23.62,
        "by_bucket": {"1-2": {"n": 2, "sd_delta": 5.0}},
    }
    assert reach_sd_for({}, thin_generic, round_num=1) == approx(23.62)
    # no generic bucket at all -> generic reach_sd
    assert reach_sd_for({}, {"reach_sd": 9.0}, round_num=1) == approx(9.0)
    # nothing at all -> reference
    assert reach_sd_for({}, {}, round_num=1) == REACH_SD_REFERENCE


# --- the downstream effect: candidate_weights -----------------------------------


def test_round1_reach_sd_concentrates_more_than_round12():
    sd_round1 = reach_sd_for(ROUND_AWARE_OWNER, {"reach_sd": 30.0}, round_num=1)
    sd_round12 = reach_sd_for(ROUND_AWARE_OWNER, {"reach_sd": 30.0}, round_num=12)
    assert sd_round1 < sd_round12

    adps = [float(i + 1) for i in range(8)]
    w1 = candidate_weights(adps, 1, sd_round1)
    w12 = candidate_weights(adps, 144, sd_round12)

    top1 = w1[0] / sum(w1)
    top12 = w12[0] / sum(w12)
    # round 1 puts MORE probability on the single best candidate
    assert top1 > top12


# --- recency weighting still applies inside buckets -----------------------------


def test_recency_weighting_applies_inside_buckets():
    # five +5 deltas in 2024 (weight 1.0), five -5 deltas in 2023 (weight 0.9)
    recent = [event(5, "1-2", season=2024) for _ in range(5)]
    older = [event(-5, "1-2", season=2023) for _ in range(5)]
    stats = _reach_stats(recent + older, weight_of)
    bucket = stats["by_bucket"]["1-2"]

    total_w = 5 * 1.0 + 5 * 0.9
    weighted_mean = (5 * 5.0 + 5 * -5.0 * 0.9) / total_w
    assert bucket["mean_delta"] == approx(round(weighted_mean, 2))
    # if weighting were dropped, the mean would be exactly 0.0
    assert bucket["mean_delta"] != approx(0.0, abs=1e-6)


# --- build_generic_tendencies pools per-bucket ---------------------------------
#
# Owner tendencies are scoped per ESPN league, so build_generic_tendencies
# pools each profile's by_league[str(LEAGUE)]['reach'] block. The metrics
# dicts below are wrapped in a by_league block to match the real profile
# shape; the pooling math itself is unchanged, so every assertion below is
# the same as before the per-league change.
GENERIC_LEAGUE = 111


def _wrap(reach):
    return {"by_league": {str(GENERIC_LEAGUE): {"reach": reach}}}


def test_build_generic_tendencies_pools_per_bucket():
    metrics_list = [
        _wrap(
            {
                "n": 30,
                "mean_delta": -2.0,
                "sd_delta": 23.0,
                "by_bucket": {
                    "1-2": {"n": 6, "mean_delta": -1.0, "sd_delta": 5.0},
                    "10+": {"n": 24, "mean_delta": -3.0, "sd_delta": 28.0},
                },
            }
        ),
        _wrap(
            {
                "n": 10,
                "mean_delta": 0.0,
                "sd_delta": 20.0,
                "by_bucket": {
                    "1-2": {"n": 4, "mean_delta": 1.0, "sd_delta": 7.0},
                    "10+": {"n": 6, "mean_delta": -2.0, "sd_delta": 30.0},
                },
            }
        ),
    ]
    generic = build_generic_tendencies(metrics_list, GENERIC_LEAGUE)
    # existing keys unchanged
    assert generic["reach_sd"] == approx((30 * 23 + 10 * 20) / 40)
    assert generic["n"] == 40

    # per-bucket pooled by sample size
    b = generic["by_bucket"]
    assert b["1-2"]["sd_delta"] == approx((6 * 5 + 4 * 7) / 10)
    assert b["1-2"]["n"] == 10
    assert b["10+"]["sd_delta"] == approx((24 * 28 + 6 * 30) / 30)
    assert b["10+"]["n"] == 30


def test_build_generic_tendencies_keeps_keys_when_no_buckets():
    generic = build_generic_tendencies(
        [_wrap({"n": 25, "sd_delta": 7.0, "mean_delta": -1.0})],
        GENERIC_LEAGUE,
    )
    assert "by_bucket" not in generic
    assert generic["reach_sd"] == approx(7.0)
    assert generic["n"] == 25
