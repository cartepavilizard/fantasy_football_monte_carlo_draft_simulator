# -*- coding: utf-8 -*-
"""
Backtest (the Phase 4 ship gate): on synthetic history with one owner
whose behavior contradicts the league consensus, the profile arm must
beat the generic arm on position hit rate — measured leave-one-season-out
so nothing evaluates on its own training data.
"""
from pytest import approx

from backtest import evaluate
from test_owner_profiling import pick

# 4 owners, 9 seasons, 2 rounds, rotating draft order. Owner A always
# drafts RB; B, C, D always draft WR. Because A's slot rotates, the
# pick-number-only logistic regression sees ~25% RB at every slot and
# predicts WR everywhere — A's profile is the only way to get A right.
OWNERS = ["A", "B", "C", "D"]


def synthetic_history(league=1, seasons=range(2016, 2025), verified=True):
    picks = []
    for season in seasons:
        order = OWNERS[season % 4:] + OWNERS[: season % 4]
        overall = 0
        for round_num in (1, 2):
            for slot, owner in enumerate(order, start=1):
                overall += 1
                position = "rb" if owner == "A" else "wr"
                picks.append(
                    pick(
                        overall,
                        round_num,
                        guid=owner,
                        position=position,
                        season=season,
                        league=league,
                        adp=float(overall),  # a perfectly calibrated market
                        name=f"S{season}-P{overall}",
                        display=f"Owner {owner}",
                        verified=verified,
                    )
                )
    return picks


def test_profile_arm_beats_generic_on_contrarian_owner():
    result = evaluate(synthetic_history(), top_k=3)
    assert len(result["seasons_evaluated"]) == 9
    generic_rate = result["generic"]["position_hit_rate"]
    profile_rate = result["profile"]["position_hit_rate"]
    # Generic gets B/C/D right and A wrong -> 75%
    assert generic_rate == approx(0.75, abs=0.02)
    # Profiles recover A's picks
    assert profile_rate > generic_rate
    assert result["position_hit_rate_improvement"] >= 0.2
    # Player-level metric exists and profiles don't do worse
    assert result["profile"]["player_top3_rate"] >= result["generic"]["player_top3_rate"]


def test_auction_and_thin_seasons_are_skipped():
    history = synthetic_history()
    # an auction season in another league, and a lone thin season in a third
    history += [
        pick(1, 1, guid="A", position="rb", season=2020, league=2, bid=30),
        pick(1, 1, guid="A", position="rb", season=2020, league=3),
    ]
    result = evaluate(history, top_k=3)
    reasons = {(s["league"], s["why"]) for s in result["seasons_skipped"]}
    assert (2, "auction") in reasons
    assert (3, "thin training data") in reasons
    assert len(result["seasons_evaluated"]) == 9


def test_backtest_endpoint_requires_history(client):
    response = client.post("/owners/backtest")
    assert response.status_code == 400


# Each verified league-season contributes 8 picks (4 owners x 2 rounds).
PICKS_PER_SEASON = 8
SEASONS = 9
PICKS_PER_LEAGUE = PICKS_PER_SEASON * SEASONS  # 72


def test_unverified_league_excluded_by_default():
    """Default verified_order_only=True drops a second league's
    unverified seasons before season grouping, so they are neither
    evaluated nor skipped-with-a-reason -- they are simply absent."""
    history = synthetic_history(league=1) + synthetic_history(
        league=2, verified=False
    )
    result = evaluate(history, top_k=3)
    ov = result["order_verification"]
    assert ov["verified_order_only"] is True
    assert ov["picks_supplied"] == PICKS_PER_LEAGUE * 2
    assert ov["picks_used"] == PICKS_PER_LEAGUE
    assert ov["picks_dropped_unverified"] == PICKS_PER_LEAGUE
    assert ov["leagues_evaluated"] == [1]
    assert {e["league"] for e in result["seasons_evaluated"]} == {1}
    # league 2 was dropped before grouping, so it is not in the skipped
    # log either -- the gate is silent because it runs before the loop
    assert [s for s in result["seasons_skipped"] if s["league"] == 2] == []
    assert len(result["seasons_evaluated"]) == SEASONS


def test_verified_order_only_false_admits_unverified_league():
    """verified_order_only=False is a real switch: the same mixed history
    now evaluates the unverified league's seasons (it has >=2 positions and
    enough training picks to clear the thin-data skip)."""
    history = synthetic_history(league=1) + synthetic_history(
        league=2, verified=False
    )
    result = evaluate(history, top_k=3, verified_order_only=False)
    ov = result["order_verification"]
    assert ov["verified_order_only"] is False
    assert ov["picks_supplied"] == PICKS_PER_LEAGUE * 2
    assert ov["picks_used"] == PICKS_PER_LEAGUE * 2
    assert ov["picks_dropped_unverified"] == 0
    assert sorted(ov["leagues_evaluated"]) == [1, 2]
    evaluated_by_league = {
        league: [e for e in result["seasons_evaluated"] if e["league"] == league]
        for league in (1, 2)
    }
    assert len(evaluated_by_league[1]) == SEASONS
    assert len(evaluated_by_league[2]) == SEASONS


def test_order_verification_block_reports_counts():
    """A focused check that supplied/used/dropped agree arithmetically
    across both flag states on the same mixed history."""
    history = synthetic_history(league=1) + synthetic_history(
        league=2, verified=False
    )
    on = evaluate(history, top_k=3)
    off = evaluate(history, top_k=3, verified_order_only=False)
    assert on["order_verification"]["picks_supplied"] == off["order_verification"][
        "picks_supplied"
    ]
    assert on["order_verification"]["picks_used"] == PICKS_PER_LEAGUE
    assert off["order_verification"]["picks_used"] == PICKS_PER_LEAGUE * 2
    # dropped = supplied - used, and is zero when the gate is off
    assert (
        on["order_verification"]["picks_dropped_unverified"]
        == on["order_verification"]["picks_supplied"]
        - on["order_verification"]["picks_used"]
    )
    assert off["order_verification"]["picks_dropped_unverified"] == 0


def test_evaluate_leagues_restricts_evaluated_leagues():
    """evaluate_leagues scopes which leagues are EVALUATED; the logistic
    regression still trains only on each evaluated league's own other
    seasons, so the generic arm for league 1 is unchanged by league 2's
    presence in the pool."""
    league1 = synthetic_history(league=1)
    league2 = synthetic_history(league=2)  # verified=True, stays in pool
    alone = evaluate(league1, top_k=3)
    selected = evaluate(league1 + league2, top_k=3, evaluate_leagues=[1])

    assert {e["league"] for e in selected["seasons_evaluated"]} == {1}
    assert len(selected["seasons_evaluated"]) == SEASONS
    assert selected["order_verification"]["leagues_evaluated"] == [1]
    # league 2's seasons are recorded as skipped, not silently dropped
    league2_skips = [s for s in selected["seasons_skipped"] if s["league"] == 2]
    assert len(league2_skips) == SEASONS
    assert all(s["why"] == "league not selected" for s in league2_skips)

    # Training intact: regression training draws only on league 1's own
    # other seasons, so the generic arm is identical whether or not
    # league 2 is in the pool (the profile arm may differ, which is fine)
    assert selected["generic"]["position_hit_rate"] == alone["generic"][
        "position_hit_rate"
    ]
    assert selected["generic"]["position_n"] == alone["generic"]["position_n"]
    assert selected["generic"]["player_top3_n"] == alone["generic"]["player_top3_n"]
