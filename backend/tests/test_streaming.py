# -*- coding: utf-8 -*-
"""
C3: K/DST streaming recommendations. Pins down the ranking contract —
filter to K/DST, rank by C2's matchup-adjusted points, tie-break by the
raw multiplier — and C9's homer check at this call site. strength is
hand-built (same injectable seam as optimize_lineup()) so these tests
pin the streaming logic itself, not C2's shrinkage math (that's
test_matchup_strength.py's job).
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.config import DRAFT_YEAR, HOMER_TEAM
from models.inseason import FreeAgentEntry, FreeAgentSnapshot
from models.streaming import streaming_recommendations

SEASON = DRAFT_YEAR
LEAGUE_ID = 111
WEEK = 5


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-streaming")


def neutral_entry(multiplier=1.0, weeks=0, confidence="none", rank=None):
    return {
        "multiplier": multiplier,
        "observed_ratio": None,
        "weeks_sampled": weeks,
        "confidence": confidence,
        "rank": rank,
    }


async def seed_free_agents(engine, entries):
    await engine.save(
        FreeAgentSnapshot(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=WEEK,
            entries=entries,
        )
    )


def fa(pid, name, position, team, projected):
    return FreeAgentEntry(
        player_id=pid,
        player_name=name,
        position=position,
        nfl_team=team,
        projected_points=projected,
    )


def run(engine, strength=None, opponents=None):
    return asyncio.run(
        streaming_recommendations(
            engine, LEAGUE_ID, SEASON, WEEK, strength=strength, opponents=opponents
        )
    )


def test_no_snapshot_returns_empty_list():
    engine = make_engine()
    result = run(engine)
    assert result == {"week": WEEK, "recommendations": []}


def test_filters_to_k_and_dst_only():
    engine = make_engine()
    asyncio.run(
        seed_free_agents(
            engine,
            [
                fa(1, "QB One", "QB", "KC", 20.0),
                fa(2, "Chiefs D/ST", "DST", "KC", 8.0),
                fa(3, "Kicker One", "K", "DET", 7.5),
            ],
        )
    )
    result = run(engine, strength={"positions": {}}, opponents={})
    names = {row["player_name"] for row in result["recommendations"]}
    assert names == {"Chiefs D/ST", "Kicker One"}


def test_ranks_by_matchup_adjusted_points():
    engine = make_engine()
    asyncio.run(
        seed_free_agents(
            engine,
            [
                fa(1, "Soft Matchup DST", "DST", "KC", 8.0),
                fa(2, "Tough Matchup DST", "DST", "SF", 8.0),
            ],
        )
    )
    strength = {
        "positions": {
            "DST": {
                "BUF": neutral_entry(1.3, weeks=6, confidence="high", rank=1),
                "SEA": neutral_entry(0.7, weeks=6, confidence="high", rank=32),
            }
        }
    }
    opponents = {("KC", WEEK): "BUF", ("SF", WEEK): "SEA"}
    result = run(engine, strength=strength, opponents=opponents)
    ordered = [row["player_name"] for row in result["recommendations"]]
    assert ordered == ["Soft Matchup DST", "Tough Matchup DST"]
    assert result["recommendations"][0]["rank"] == 1
    assert result["recommendations"][0]["matchup"]["multiplier"] == 1.3
    # 8.0 * 1.10 (alpha 0.5 * 30% tilt, capped at 10%)
    assert result["recommendations"][0]["matchup_adjusted_points"] == 8.8


def test_ties_on_adjusted_points_break_by_raw_multiplier():
    engine = make_engine()
    asyncio.run(
        seed_free_agents(
            engine,
            [
                fa(1, "Extreme Soft DST", "DST", "KC", 8.0),
                fa(2, "Mild Soft DST", "DST", "SF", 8.0),
            ],
        )
    )
    # both tilts clamp to the same +10% cap (0.5 * 0.30 and 0.5 * 0.25
    # both exceed the 10% max_tilt), so adjusted points tie exactly —
    # the tie-break must fall back to the raw (uncapped) multiplier
    strength = {
        "positions": {
            "DST": {
                "BUF": neutral_entry(1.3, weeks=6, confidence="high", rank=1),
                "NYJ": neutral_entry(1.25, weeks=6, confidence="high", rank=2),
            }
        }
    }
    opponents = {("KC", WEEK): "BUF", ("SF", WEEK): "NYJ"}
    result = run(engine, strength=strength, opponents=opponents)
    ordered = [row["player_name"] for row in result["recommendations"]]
    adjusted = {row["player_name"]: row["matchup_adjusted_points"] for row in result["recommendations"]}
    assert adjusted["Extreme Soft DST"] == adjusted["Mild Soft DST"]
    assert ordered == ["Extreme Soft DST", "Mild Soft DST"]


def test_bye_week_free_agent_gets_neutral_matchup():
    engine = make_engine()
    asyncio.run(seed_free_agents(engine, [fa(1, "Bye Kicker", "K", "KC", 7.0)]))
    result = run(engine, strength={"positions": {}}, opponents={})
    row = result["recommendations"][0]
    assert row["opponent"] is None
    assert row["matchup"]["multiplier"] == 1.0
    assert row["matchup_adjusted_points"] == 7.0


def test_homer_check_attached_for_homer_team_free_agent():
    engine = make_engine()
    asyncio.run(
        seed_free_agents(
            engine,
            [
                fa(1, f"{HOMER_TEAM} D/ST", "DST", HOMER_TEAM, 9.0),
                fa(2, "Alt DST One", "DST", "BUF", 7.0),
                fa(3, "Alt DST Two", "DST", "MIA", 6.0),
            ],
        )
    )
    result = run(engine, strength={"positions": {}}, opponents={})
    by_name = {row["player_name"]: row for row in result["recommendations"]}
    homer_row = by_name[f"{HOMER_TEAM} D/ST"]
    assert homer_row["homer_check"] is not None
    assert homer_row["homer_check"]["homer_team"] == HOMER_TEAM
    assert homer_row["homer_check"]["suggested"]["name"] == f"{HOMER_TEAM} D/ST"
    alt_names = {row["name"] for row in homer_row["homer_check"]["alternatives"]}
    assert alt_names == {"Alt DST One", "Alt DST Two"}
    # only the homer-team row gets a check
    assert by_name["Alt DST One"]["homer_check"] is None


def test_homer_check_absent_without_alternatives():
    """No same-position alternatives means nothing to compare against"""
    engine = make_engine()
    asyncio.run(
        seed_free_agents(
            engine, [fa(1, f"{HOMER_TEAM} D/ST", "DST", HOMER_TEAM, 9.0)]
        )
    )
    result = run(engine, strength={"positions": {}}, opponents={})
    assert result["recommendations"][0]["homer_check"] is None
