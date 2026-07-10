# -*- coding: utf-8 -*-
"""
Owner tendency extraction: hand-computed frequency/average metrics with
recency weighting, sample sizes on every metric, and the exclusion rules
(keepers, auction seasons) from the architecture review.
"""
from pytest import approx

from models.sources import HistoricalPick
from profiling import bucket_for_round, extract_profiles


def pick(
    overall,
    round_num,
    guid="G1",
    position="rb",
    adp=None,
    season=2024,
    league=1,
    keeper=False,
    bid=None,
    name="Some Player",
    display="Dave",
):
    return HistoricalPick(
        espn_league_id=league,
        season=season,
        overall_pick=overall,
        round_num=round_num,
        round_pick=overall,
        member_guid=guid,
        owner_display_name=display,
        espn_team_id=1,
        raw_player_name=name,
        position=position,
        is_keeper=keeper,
        bid_amount=bid,
        historical_adp=adp,
    )


def profile_of(picks, key="G1", **kwargs):
    profiles = extract_profiles(picks, **kwargs)
    return next(p for p in profiles if p.profile_key == key)


def test_round_buckets():
    assert bucket_for_round(1) == "1-2"
    assert bucket_for_round(2) == "1-2"
    assert bucket_for_round(3) == "3-5"
    assert bucket_for_round(9) == "6-9"
    assert bucket_for_round(10) == "10+"
    assert bucket_for_round(16) == "10+"


def test_position_frequency_is_recency_weighted_with_raw_n():
    picks = [
        pick(1, 1, position="rb", season=2024),
        pick(4, 2, position="rb", season=2024),
        pick(1, 1, position="wr", season=2023),
        pick(4, 2, position="rb", season=2023),
    ]
    profile = profile_of(picks, current_season=2024)
    bucket = profile.metrics["position_frequency"]["1-2"]
    # weights: 2024 -> 1.0 each, 2023 -> 0.9 each
    # rb = 1 + 1 + 0.9 = 2.9 of 3.8 total
    assert bucket["n"] == 4
    assert bucket["shares"]["rb"] == approx(2.9 / 3.8, abs=1e-3)
    assert bucket["shares"]["wr"] == approx(0.9 / 3.8, abs=1e-3)
    assert profile.metrics["position_frequency"]["10+"] == {"n": 0, "shares": {}}


def test_reach_stats_mean_sd_and_rate():
    picks = [
        pick(1, 1, adp=8.0),  # delta -7: a reach past the 6-pick threshold
        pick(3, 2, adp=2.0),  # delta +1: value fell to them
        pick(5, 3, adp=None),  # no market data -> excluded from reach n
    ]
    reach = profile_of(picks, current_season=2024).metrics["reach"]
    assert reach["n"] == 2
    assert reach["mean_delta"] == approx(-3.0)
    assert reach["sd_delta"] == approx(4.0)
    assert reach["reach_rate"] == approx(0.5)
    assert reach["threshold_picks"] == 6


def test_run_participation_counts_only_run_opportunities():
    board = [
        # picks 1-5: a WR run (3 of last 5) in front of G1's pick 6
        pick(1, 1, guid="G2", position="wr"),
        pick(2, 1, guid="G2", position="wr"),
        pick(3, 2, guid="G2", position="wr"),
        pick(4, 2, guid="G2", position="rb"),
        pick(5, 3, guid="G2", position="te"),
        pick(6, 3, guid="G1", position="wr"),  # joins the run
        # picks 7-11: an RB run in front of G1's pick 12
        pick(7, 4, guid="G2", position="rb"),
        pick(8, 4, guid="G2", position="rb"),
        pick(9, 5, guid="G2", position="rb"),
        pick(10, 5, guid="G2", position="wr"),
        pick(11, 6, guid="G2", position="qb"),
        pick(12, 6, guid="G1", position="te"),  # ignores the run
    ]
    runs = profile_of(board, current_season=2024).metrics["run_participation"]
    assert runs["n"] == 2
    assert runs["rate"] == approx(0.5)


def test_post_miss_is_inferred_and_compared_to_own_baseline():
    board = [
        # A plausible TE target (ADP 11, inside [8, 16]) sniped 2 picks
        # before G1's turn at overall 10
        pick(8, 10, guid="G2", position="te", adp=11.0),
        pick(9, 10, guid="G2", position="rb", adp=None),
        pick(10, 10, guid="G1", position="te"),  # chases anyway
        # another G1 pick in the same 10+ bucket to set the baseline
        pick(20, 11, guid="G1", position="k"),
    ]
    post_miss = profile_of(board, current_season=2024).metrics["post_miss"]
    assert post_miss["inferred"] is True
    assert post_miss["n"] == 1
    assert post_miss["chase_rate"] == approx(1.0)
    # G1's own 10+ bucket is 50% te (the chase pick and the k pick)
    assert post_miss["baseline_share"] == approx(0.5)
    assert post_miss["shift"] == approx(0.5)


def test_onesie_timing_uses_first_round_per_season():
    picks = [
        pick(9, 5, position="qb", season=2024),
        pick(17, 9, position="qb", season=2024),  # second QB ignored (min)
        pick(13, 7, position="qb", season=2023),
        pick(27, 14, position="k", season=2024),
    ]
    timing = profile_of(picks, current_season=2024).metrics["onesie_timing"]
    qb = timing["qb"]
    assert qb["n"] == 2
    assert (qb["earliest"], qb["latest"]) == (5, 7)
    # weighted: (5*1.0 + 7*0.9) / 1.9
    assert qb["mean_first_round"] == approx(11.3 / 1.9, abs=0.01)
    assert timing["k"]["n"] == 1
    assert timing["te"] == {"n": 0}


def test_keepers_are_context_but_not_owner_choices():
    picks = [
        pick(1, 1, position="rb"),
        pick(2, 1, position="wr", keeper=True),  # not a choice
    ]
    profile = profile_of(picks, current_season=2024)
    assert profile.total_picks_observed == 1


def test_auction_seasons_are_excluded_entirely():
    picks = [
        pick(1, 1, position="rb", season=2024),  # snake season counts
        pick(1, 1, position="qb", season=2023, bid=57),  # auction season
        pick(2, 1, position="qb", season=2023, guid="G3", display="Auction Al"),
    ]
    profiles = extract_profiles(picks, current_season=2024)
    by_key = {profile.profile_key: profile for profile in profiles}
    assert by_key["G1"].total_picks_observed == 1
    assert 2023 not in by_key["G1"].seasons_observed
    # An owner seen only in auction seasons gets no profile at all
    assert "G3" not in by_key


def test_alias_map_merges_guids_into_one_profile():
    picks = [
        pick(1, 1, guid="G-OLD", display="Dave (old account)", league=1),
        pick(1, 1, guid="G-NEW", display="Dave", league=2),
    ]
    profiles = extract_profiles(
        picks, alias_map={"G-OLD": "dave", "G-NEW": "dave"}, current_season=2024
    )
    assert len(profiles) == 1
    dave = profiles[0]
    assert dave.profile_key == "dave"
    assert dave.member_guids == ["G-NEW", "G-OLD"]
    assert dave.espn_league_ids == [1, 2]
    assert dave.total_picks_observed == 2
