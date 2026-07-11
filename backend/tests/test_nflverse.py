# -*- coding: utf-8 -*-
"""
C4 (cheap half): nflverse usage ingestion. The contract under test:
snap-counts is the spine, player-stats fills target/carry fields onto
matching (player, week) rows, REG-only filtering, blank -> None, team
abbreviation normalization (nflverse's LA -> ESPN's LAR), per-week
replace-not-duplicate semantics, and per-source failure logging that
never raises. Scheduler wiring (guard off-by-default, most-recent-
completed-week selection) is covered in test_inseason_scheduler.py.
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from data_sources.nflverse import NflverseUsageAdapter, ingest_usage
from models.inseason import LeagueSyncLog, PlayerWeekUsage
from tests.conftest import ScriptedTransport

SEASON = 2024

SNAP_HEADER = "week,player,position,team,opponent,game_type,offense_snaps,offense_pct"
STATS_HEADER = (
    "player_display_name,position,recent_team,week,season_type,"
    "targets,target_share,carries,receptions"
)


def snap_csv(*rows: str) -> str:
    return "\n".join([SNAP_HEADER, *rows])


def stats_csv(*rows: str) -> str:
    return "\n".join([STATS_HEADER, *rows])


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-nflverse")


def run(coro):
    return asyncio.run(coro)


async def _usage_rows(engine):
    return await engine.find(PlayerWeekUsage)


# --- adapter parsing -----------------------------------------------------------


def test_fetch_snap_counts_filters_reg_and_parses_fields():
    transport = ScriptedTransport(
        [
            (
                200,
                snap_csv(
                    "5,Backup Back,RB,SEA,ARI,REG,45,0.83",
                    "5,Playoff Guy,RB,SEA,ARI,POST,40,0.75",
                ),
            )
        ]
    )
    adapter = NflverseUsageAdapter(transport=transport)

    (row,) = run(adapter.fetch_snap_counts(SEASON))
    assert row.player_name == "Backup Back"
    assert row.week == 5
    assert row.nfl_team == "SEA"
    assert row.opponent == "ARI"
    assert row.snaps == 45
    assert row.snap_share == 0.83


def test_fetch_snap_counts_blank_numeric_fields_become_none():
    transport = ScriptedTransport(
        [(200, snap_csv("5,No Snaps Listed,RB,SEA,ARI,REG,,"))]
    )
    adapter = NflverseUsageAdapter(transport=transport)

    (row,) = run(adapter.fetch_snap_counts(SEASON))
    assert row.snaps is None
    assert row.snap_share is None


def test_fetch_snap_counts_normalizes_la_to_rams():
    transport = ScriptedTransport(
        [(200, snap_csv("5,Ram Runner,RB,LA,SEA,REG,30,0.60"))]
    )
    adapter = NflverseUsageAdapter(transport=transport)

    (row,) = run(adapter.fetch_snap_counts(SEASON))
    assert row.nfl_team == "LAR"  # nflverse's LA -> ESPN's canonical LAR


def test_fetch_player_stats_filters_reg_and_computes_touches():
    transport = ScriptedTransport(
        [
            (
                200,
                stats_csv(
                    "Backup Back,RB,SEA,5,REG,6,0.28,10,4",
                    "Backup Back,RB,SEA,5,POST,6,0.28,10,4",
                ),
            )
        ]
    )
    adapter = NflverseUsageAdapter(transport=transport)

    (row,) = run(adapter.fetch_player_stats(SEASON))
    assert row.targets == 6
    assert row.target_share == 0.28
    assert row.carries == 10
    assert row.touches == 14  # carries + receptions


# --- ingest_usage: merge + replace semantics ------------------------------------


def test_ingest_usage_merges_snap_spine_with_stats_by_player_and_week():
    transport = ScriptedTransport(
        [
            (200, snap_csv("5,Backup Back,RB,SEA,ARI,REG,45,0.83")),
            (200, stats_csv("Backup Back,RB,SEA,5,REG,6,0.28,10,4")),
        ]
    )
    engine = make_engine()

    run(ingest_usage(engine, SEASON, adapter=NflverseUsageAdapter(transport=transport)))

    (row,) = run(_usage_rows(engine))
    assert row.snap_share == 0.83  # from the snap-count spine
    assert row.target_share == 0.28  # filled in from player-stats
    assert row.touches == 14


def test_ingest_usage_stats_only_player_is_dropped_not_a_spine_row():
    """The stats file alone can't seed a row — the snap file is the spine"""
    transport = ScriptedTransport(
        [
            (200, snap_csv("5,Backup Back,RB,SEA,ARI,REG,45,0.83")),
            (
                200,
                stats_csv(
                    "Backup Back,RB,SEA,5,REG,6,0.28,10,4",
                    "Kicker Only,K,SEA,5,REG,,,,",
                ),
            ),
        ]
    )
    engine = make_engine()

    run(ingest_usage(engine, SEASON, adapter=NflverseUsageAdapter(transport=transport)))

    rows = run(_usage_rows(engine))
    assert [r.player_name for r in rows] == ["Backup Back"]


def test_ingest_usage_replace_not_duplicate_on_rerun():
    def transport():
        return ScriptedTransport(
            [
                (200, snap_csv("5,Backup Back,RB,SEA,ARI,REG,45,0.83")),
                (200, stats_csv("Backup Back,RB,SEA,5,REG,6,0.28,10,4")),
            ]
        )

    engine = make_engine()
    run(ingest_usage(engine, SEASON, adapter=NflverseUsageAdapter(transport=transport())))
    run(ingest_usage(engine, SEASON, adapter=NflverseUsageAdapter(transport=transport())))

    rows = run(_usage_rows(engine))
    assert len(rows) == 1  # re-ingesting the same week replaces, not appends


def test_ingest_usage_replace_scope_is_per_week():
    def transport():
        return ScriptedTransport(
            [
                (
                    200,
                    snap_csv(
                        "4,Backup Back,RB,SEA,ARI,REG,40,0.70",
                        "5,Backup Back,RB,SEA,SF,REG,45,0.83",
                    ),
                ),
                (200, stats_csv()),
            ]
        )

    engine = make_engine()
    run(ingest_usage(engine, SEASON, adapter=NflverseUsageAdapter(transport=transport())))
    # re-ingest only week 5 with an updated share; week 4 must survive untouched
    transport2 = ScriptedTransport(
        [
            (200, snap_csv("5,Backup Back,RB,SEA,SF,REG,48,0.90")),
            (200, stats_csv()),
        ]
    )
    run(
        ingest_usage(
            engine, SEASON, week=5, adapter=NflverseUsageAdapter(transport=transport2)
        )
    )

    rows = {r.week: r for r in run(_usage_rows(engine))}
    assert rows[4].snap_share == 0.70
    assert rows[5].snap_share == 0.90


# --- per-source failure logging -------------------------------------------------


def test_ingest_usage_logs_stats_failure_and_still_saves_snap_spine():
    transport = ScriptedTransport(
        [
            (200, snap_csv("5,Backup Back,RB,SEA,ARI,REG,45,0.83")),
            (500, "server error"),
        ]
    )
    engine = make_engine()

    summary = run(
        ingest_usage(engine, SEASON, adapter=NflverseUsageAdapter(transport=transport))
    )

    assert summary["sources"]["player_stats"]["success"] is False
    (row,) = run(_usage_rows(engine))
    assert row.snap_share == 0.83
    assert row.target_share is None  # stats source failed; spine alone persists

    async def _sync_logs():
        return await engine.find(LeagueSyncLog)

    (log,) = run(_sync_logs())
    assert log.section == "usage_player_stats"
    assert log.success is False
    assert log.espn_league_id is None


def test_ingest_usage_snap_failure_writes_nothing():
    transport = ScriptedTransport(
        [
            (500, "server error"),
            (200, stats_csv("Backup Back,RB,SEA,5,REG,6,0.28,10,4")),
        ]
    )
    engine = make_engine()

    summary = run(
        ingest_usage(engine, SEASON, adapter=NflverseUsageAdapter(transport=transport))
    )

    assert summary["sources"]["snap_counts"]["success"] is False
    assert run(_usage_rows(engine)) == []  # no spine, nothing to merge onto
