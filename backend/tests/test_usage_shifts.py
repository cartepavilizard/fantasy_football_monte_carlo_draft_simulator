# -*- coding: utf-8 -*-
"""
C4 (frontier core): the usage-shift signal. The contract under test:
current week vs a 2-4-week trailing baseline, absolute share-point
thresholds (snap 0.12 / target 0.07), relevance floors, no alerts
before a real baseline exists (week 3 at the earliest), and
process-over-results copy — volume and opportunity, never points.
The nflverse ingestion that fills PlayerWeekUsage is the cheap half.
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.config import DRAFT_YEAR
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    PlayerWeekUsage,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.notifications import Notification
from models.usage_shifts import (
    detect_usage_shifts,
    ensure_usage_shift_notifications,
    relevant_player_names,
    variance_note,
)

SEASON = DRAFT_YEAR


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-usage")


def usage(
    engine,
    player,
    week,
    snap=None,
    target=None,
    team="SEA",
    pos="RB",
    targets=None,
    carries=None,
    touches=None,
):
    return engine.save(
        PlayerWeekUsage(
            season=SEASON,
            week=week,
            player_name=player,
            position=pos,
            nfl_team=team,
            snap_share=snap,
            target_share=target,
            targets=targets,
            carries=carries,
            touches=touches,
        )
    )


def detect(engine, week):
    return asyncio.run(detect_usage_shifts(engine, SEASON, week))


def test_rising_snap_share_is_detected_with_exact_baseline():
    engine = make_engine()

    async def seed():
        for week, share in [(2, 0.35), (3, 0.40), (4, 0.36), (5, 0.58)]:
            await usage(engine, "Backup Back", week, snap=share)

    asyncio.run(seed())
    (shift,) = detect(engine, 5)
    assert shift["metric"] == "snap_share"
    assert shift["direction"] == "rising"
    assert shift["baseline"] == 0.37  # mean of weeks 2-4
    assert shift["delta"] == 0.21
    assert shift["baseline_weeks"] == 3


def test_falling_target_share_is_detected():
    engine = make_engine()

    async def seed():
        for week, share in [(3, 0.26), (4, 0.24), (5, 0.15)]:
            await usage(engine, "Fading Wideout", week, target=share, pos="WR")

    asyncio.run(seed())
    (shift,) = detect(engine, 5)
    assert (shift["metric"], shift["direction"]) == ("target_share", "falling")
    assert shift["baseline"] == 0.25
    assert shift["delta"] == -0.1


def test_sub_threshold_moves_are_noise():
    engine = make_engine()

    async def seed():
        # snap +0.11 < 0.12; target +0.06 < 0.07
        for week, snap, target in [(3, 0.50, 0.20), (4, 0.50, 0.20), (5, 0.61, 0.26)]:
            await usage(engine, "Steady Eddie", week, snap=snap, target=target)

    asyncio.run(seed())
    assert detect(engine, 5) == []


def test_no_baseline_before_two_prior_weeks():
    """Week 1 -> 2 is matchup script, not a role change: the first
    possible alert is week 3"""
    engine = make_engine()

    async def seed():
        await usage(engine, "September Riser", 1, snap=0.30)
        await usage(engine, "September Riser", 2, snap=0.60)

    asyncio.run(seed())
    assert detect(engine, 2) == []

    asyncio.run(usage(engine, "September Riser", 3, snap=0.62))
    (shift,) = detect(engine, 3)
    assert shift["week"] == 3
    assert shift["baseline_weeks"] == 2


def test_relevance_floor_ignores_bottom_of_roster_churn():
    engine = make_engine()

    async def seed():
        # 0% -> 13%: over the 0.12 threshold but under the 0.15 floor
        for week, share in [(3, 0.0), (4, 0.0), (5, 0.13)]:
            await usage(engine, "Fourth Stringer", week, snap=share)

    asyncio.run(seed())
    assert detect(engine, 5) == []


def test_baseline_window_is_the_last_four_weeks():
    engine = make_engine()

    async def seed():
        # weeks 1-2 (huge role) must NOT drag the baseline: window is 5-8
        for week, share in [(1, 0.90), (2, 0.90)]:
            await usage(engine, "Recovering Starter", week, snap=share)
        for week, share in [(5, 0.30), (6, 0.30), (7, 0.30), (8, 0.30)]:
            await usage(engine, "Recovering Starter", week, snap=share)
        await usage(engine, "Recovering Starter", 9, snap=0.55)

    asyncio.run(seed())
    (shift,) = detect(engine, 9)
    assert shift["baseline"] == 0.3
    assert shift["baseline_weeks"] == 4


def test_notifications_use_volume_language_and_dedupe():
    engine = make_engine()

    async def seed():
        for week, share in [(3, 0.35), (4, 0.37), (5, 0.58)]:
            await usage(engine, "Backup Back", week, snap=share)
        # make him actionable: he sits in a synced free-agent pool
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=111,
                season=SEASON,
                week=5,
                entries=[
                    FreeAgentEntry(player_id=1, player_name="Backup Back")
                ],
            )
        )

    asyncio.run(seed())
    created = asyncio.run(ensure_usage_shift_notifications(engine, SEASON, 5))
    (notification,) = created
    assert notification.kind == "usage_shift"
    assert "snap share" in notification.title
    assert "58%" in notification.body and "36%" in notification.body
    # process over results: opportunity language, never points
    for text in (notification.title, notification.body):
        assert "point" not in text.lower()
    # idempotent: the next sync pass creates nothing new
    assert asyncio.run(ensure_usage_shift_notifications(engine, SEASON, 5)) == []

    async def count():
        return len(await engine.find(Notification))

    assert asyncio.run(count()) == 1


def test_notifications_skip_players_not_in_any_synced_league():
    engine = make_engine()

    async def seed():
        for week, share in [(3, 0.35), (4, 0.37), (5, 0.58)]:
            await usage(engine, "Irrelevant Guy", week, snap=share)

    asyncio.run(seed())
    # the shift IS detected (trends view)...
    assert len(detect(engine, 5)) == 1
    # ...but nothing actionable is rostered/available, so no alert
    assert asyncio.run(ensure_usage_shift_notifications(engine, SEASON, 5)) == []


def test_relevant_names_union_rosters_and_free_agents():
    engine = make_engine()

    async def seed():
        await engine.save(
            TeamWeekRoster(
                espn_league_id=111,
                season=SEASON,
                week=5,
                espn_team_id=1,
                entries=[
                    RosterSlotEntry(
                        player_id=1,
                        player_name="Rostered Guy",
                        lineup_slot="RB",
                    )
                ],
            )
        )
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=222,
                season=SEASON,
                week=5,
                entries=[FreeAgentEntry(player_id=2, player_name="Pool Guy")],
            )
        )

    asyncio.run(seed())
    names = asyncio.run(relevant_player_names(engine, SEASON))
    assert names == {"Rostered Guy", "Pool Guy"}


def test_variance_note_flags_high_targets_low_catches():
    engine = make_engine()
    # BRAINSTORM §2.9's own example: 9 targets, 1 catch (touches=carries+receptions)
    row = asyncio.run(
        usage(engine, "Quiet Game", 5, targets=9, carries=0, touches=1)
    )
    note = variance_note(row)
    assert note == {"targets": 9, "receptions": 1, "catch_rate": round(1 / 9, 4)}


def test_variance_note_ignores_token_targets():
    engine = make_engine()
    # 2 targets, 0 catches clears the catch-rate ceiling but not the
    # target floor — a token target isn't a story
    row = asyncio.run(usage(engine, "Token Target", 5, targets=2, carries=0, touches=0))
    assert variance_note(row) is None


def test_variance_note_ignores_ordinary_efficient_games():
    engine = make_engine()
    # 9 targets, 6 catches = 67% catch rate — an ordinary day, not variance
    row = asyncio.run(usage(engine, "Efficient Day", 5, targets=9, carries=0, touches=6))
    assert variance_note(row) is None


def test_variance_note_needs_carries_and_touches():
    engine = make_engine()
    row = asyncio.run(usage(engine, "No Touch Data", 5, targets=9))
    assert variance_note(row) is None


def test_detect_usage_shifts_attaches_variance_to_shift_rows():
    engine = make_engine()

    async def seed():
        for week, share in [(2, 0.35), (3, 0.40), (4, 0.36)]:
            await usage(engine, "Backup Back", week, snap=share)
        # week 5 carries both the snap-share rise and the quiet-game
        # targets/touches in one row (a real PlayerWeekUsage doc has
        # every metric together, never split across two saves)
        await usage(
            engine, "Backup Back", 5, snap=0.58, targets=9, carries=0, touches=1
        )

    asyncio.run(seed())
    (shift,) = detect(engine, 5)
    assert shift["variance"] == {"targets": 9, "receptions": 1, "catch_rate": round(1 / 9, 4)}


def test_usage_shifts_endpoint_serves_from_cache(client, app_module):
    engine = app_module.engine

    async def seed():
        for week, share in [(3, 0.35), (4, 0.37), (5, 0.58)]:
            await usage(engine, "Backup Back", week, snap=share)

    asyncio.run(seed())
    payload = client.get("/inseason/usage_shifts?week=5").json()
    (shift,) = payload["shifts"]
    assert shift["player_name"] == "Backup Back"
    assert shift["direction"] == "rising"
