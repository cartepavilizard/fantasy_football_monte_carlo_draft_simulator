# -*- coding: utf-8 -*-
"""
Draft-order gating: order-dependent metrics (reach, run participation,
post-miss) consume only league-seasons whose ESPN pick order is
trustworthy; round-bucketed metrics (position frequency, onesie timing)
still use every season. Verified order is opt-in per league via the
HistoricalPick.draft_order_verified flag.
"""
from pytest import approx

from models.sources import HistoricalPick
from profiling import extract_profiles


def pick(
    overall,
    round_num,
    guid="G1",
    position="rb",
    adp=None,
    season=2024,
    league=1,
    verified=False,
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
        draft_order_verified=verified,
        bid_amount=bid,
        historical_adp=adp,
    )


def profile_of(picks, key="G1", **kwargs):
    profiles = extract_profiles(picks, **kwargs)
    return next(p for p in profiles if p.profile_key == key)


# A verified league-season board with a positional run and a plausible
# missed target, so reach / run_participation / post_miss are all non-zero.
def verified_board(guid="G1", season=2024, league=1, display="Dave"):
    return [
        # WR run (3 of first 5) in front of G1's pick 6 -> run opportunity
        pick(1, 1, guid="G2", position="wr", adp=2.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(2, 1, guid="G2", position="wr", adp=3.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(3, 2, guid="G2", position="wr", adp=4.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(4, 2, guid="G2", position="rb", adp=5.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(5, 3, guid="G2", position="te", adp=10.0, season=season,
             league=league, verified=True, display="Rival"),
        # G1 joins the WR run; overall 6 vs ADP 12 -> delta -6 (a reach)
        pick(6, 3, guid=guid, position="wr", adp=12.0, season=season,
             league=league, verified=True, display=display),
        # An RB run in front of G1's pick 12; G1 ignores it
        pick(7, 4, guid="G2", position="rb", adp=8.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(8, 4, guid="G2", position="rb", adp=9.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(9, 5, guid="G2", position="rb", adp=11.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(10, 5, guid="G2", position="wr", adp=14.0, season=season,
             league=league, verified=True, display="Rival"),
        # Plausible TE target (ADP 16 inside [10, 18]) sniped 1 pick before
        # G1's turn at 12; G1 pivots away to K -> post-miss data
        pick(11, 6, guid="G2", position="te", adp=16.0, season=season,
             league=league, verified=True, display="Rival"),
        pick(12, 7, guid=guid, position="k", adp=20.0, season=season,
             league=league, verified=True, display=display),
    ]


def unverified_picks(guid="G1", season=2023, league=2, display="Dave"):
    # Different season + league; round numbers are realistic, order is not.
    # All in round 1 (bucket "1-2") to make bucket assertions simple.
    return [
        pick(1, 1, guid=guid, position="qb", adp=30.0, season=season,
             league=league, verified=False, display=display),
        pick(2, 1, guid=guid, position="te", adp=40.0, season=season,
             league=league, verified=False, display=display),
        pick(3, 1, guid=guid, position="rb", adp=50.0, season=season,
             league=league, verified=False, display=display),
    ]


def test_order_metrics_use_only_verified_picks():
    """reach/run_participation/post_miss equal the verified-only run."""
    both = verified_board() + unverified_picks()
    verified_only = verified_board()

    full = profile_of(both, current_season=2024)
    vonly = profile_of(verified_only, current_season=2024)

    assert full.metrics["reach"] == vonly.metrics["reach"]
    assert full.metrics["run_participation"] == vonly.metrics["run_participation"]
    assert full.metrics["post_miss"] == vonly.metrics["post_miss"]


def test_round_metrics_reflect_unverified_picks():
    """position_frequency / onesie_timing DO see the unverified picks."""
    both = verified_board() + unverified_picks()
    verified_only = verified_board()

    full = profile_of(both, current_season=2024)
    vonly = profile_of(verified_only, current_season=2024)

    # total picks counts everything (verified + unverified, both non-keepers)
    assert full.total_picks_observed == vonly.total_picks_observed + 3

    # The 1-2 bucket gains 3 unverified picks (qb/te/rb in season 2023),
    # so the shares / n differ from the verified-only run.
    full_12 = full.metrics["position_frequency"]["1-2"]
    vonly_12 = vonly.metrics["position_frequency"]["1-2"]
    assert full_12["n"] == vonly_12["n"] + 3
    assert full_12["shares"] != vonly_12["shares"]

    # onesie_timing now sees a qb first-round in season 2023 that the
    # verified-only run does not -> different n for qb.
    assert full.metrics["onesie_timing"]["qb"]["n"] == 1  # only 2023 unverified
    assert vonly.metrics["onesie_timing"]["qb"]["n"] == 0


def test_zero_verified_picks_still_profiles():
    """An owner with no verified order still gets a valid profile."""
    picks = unverified_picks()
    profile = profile_of(picks, current_season=2024)

    # Round-based metrics populated from the unverified picks.
    assert profile.total_picks_observed == 3
    assert profile.metrics["position_frequency"]["1-2"]["n"] == 3
    assert profile.metrics["onesie_timing"]["qb"]["n"] == 1

    # Order-based metrics degrade to their empty-sample shape (no raise).
    assert profile.metrics["reach"] == {"n": 0}
    assert profile.metrics["run_participation"] == {"n": 0}
    assert profile.metrics["post_miss"] == {"n": 0, "inferred": True}


def test_order_verification_block_reports_counts_and_seasons():
    both = verified_board(season=2024, league=1) + unverified_picks(
        season=2023, league=2
    )
    block = profile_of(both, current_season=2024).metrics["order_verification"]

    assert block["verified_picks"] == 2  # G1's two verified picks
    assert block["total_picks"] == 5  # 2 verified + 3 unverified
    assert block["verified_seasons"] == [2024]
    assert block["unverified_seasons"] == [2023]


def test_zero_verified_picks_verification_block():
    block = profile_of(unverified_picks(), current_season=2024).metrics[
        "order_verification"
    ]
    assert block["verified_picks"] == 0
    assert block["total_picks"] == 3
    assert block["verified_seasons"] == []
    assert block["unverified_seasons"] == [2023]


def test_draft_order_verified_defaults_to_false():
    """
    The default is load-bearing, not cosmetic. Every historical pick written
    before this field existed comes back from Mongo with the key ABSENT, so
    the model default is what those rows evaluate to. A default of True would
    silently re-admit every commissioner-entered draft into the order-based
    metrics -- the precise fabricated data the flag exists to exclude -- and
    it would do so invisibly, because tests that build picks explicitly never
    exercise the default.
    """
    pick = HistoricalPick(
        espn_league_id=1,
        season=2024,
        overall_pick=1,
        round_num=1,
        round_pick=1,
        espn_team_id=1,
        raw_player_name="Somebody",
        member_guid="G1",
    )
    assert pick.draft_order_verified is False

    # And a profile built from picks that never set the flag must treat the
    # whole season as unverified.
    profile = profile_of([pick], current_season=2024)
    assert profile.metrics["order_verification"]["verified_picks"] == 0
