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


def synthetic_history(league=1, seasons=range(2016, 2025)):
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
