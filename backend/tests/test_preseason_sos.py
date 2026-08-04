# -*- coding: utf-8 -*-
"""
H8: preseason strength of schedule. The contract under test: the
nflverse-based adapter parses REG-only points-allowed-by-defense rows,
ingestion aggregates points allowed per (week, position, defense),
computes a ratio against the per-week mean, DAMPS it toward neutral
(PRESEASON_SOS_DAMPING — weaker than C2's same-season signal because a
season of roster/coaching turnover sits between source and target
season), clamps and ranks it the same way C2 does, replaces (not
duplicates) on rerun, and the read path (models/preseason_sos.py) never
imports data_sources.
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from data_sources.preseason_sos import PreseasonSosAdapter, ingest_preseason_sos
from models.config import PRESEASON_SOS_DAMPING
from models.preseason_sos import (
    PreseasonDefenseStrength,
    preseason_defense_strength,
    preseason_sos_for,
)
from tests.conftest import ScriptedTransport

SOURCE_SEASON = 2025
TARGET_SEASON = 2026

STATS_HEADER = (
    "player_display_name,position,recent_team,opponent_team,week,"
    "season_type,fantasy_points_ppr"
)


def stats_csv(*rows: str) -> str:
    return "\n".join([STATS_HEADER, *rows])


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-preseason-sos")


def run(coro):
    return asyncio.run(coro)


async def _stored_rows(engine):
    return await engine.find(PreseasonDefenseStrength)


# --- adapter parsing -------------------------------------------------------------


def test_fetch_points_allowed_filters_reg_and_unknown_position():
    transport = ScriptedTransport(
        [
            (
                200,
                stats_csv(
                    "Real Guy,RB,SEA,ARI,5,REG,20.0",
                    "Playoff Guy,RB,SEA,ARI,5,POST,99.0",
                    "Punter Guy,P,SEA,ARI,5,REG,1.0",
                ),
            )
        ]
    )
    adapter = PreseasonSosAdapter(transport=transport)

    rows = run(adapter.fetch_points_allowed(SOURCE_SEASON))
    assert len(rows) == 1
    assert rows[0] == {"week": 5, "position": "RB", "defense": "ARI", "points": 20.0}


def test_fetch_points_allowed_normalizes_defense_abbreviation():
    transport = ScriptedTransport([(200, stats_csv("Ram Runner,RB,SF,LA,5,REG,10.0"))])
    adapter = PreseasonSosAdapter(transport=transport)

    (row,) = run(adapter.fetch_points_allowed(SOURCE_SEASON))
    assert row["defense"] == "LAR"  # nflverse's LA -> canonical LAR


# --- ingestion: aggregation, damping, clamping, ranking ---------------------------


def _four_defense_two_week_rows():
    # 4 defenses (MIN_DEFENSES_SAMPLED) each week, mean points allowed = 10:
    # ARI allows exactly the mean (ratio 1.0); SF allows double (ratio 2.0,
    # softer matchup for RBs); DAL/NYG fill out the sample.
    return stats_csv(
        "A,RB,X,ARI,1,REG,10.0",
        "B,RB,X,SF,1,REG,20.0",
        "C,RB,X,DAL,1,REG,0.0",
        "D,RB,X,NYG,1,REG,10.0",
        "E,RB,X,ARI,2,REG,10.0",
        "F,RB,X,SF,2,REG,20.0",
        "G,RB,X,DAL,2,REG,0.0",
        "H,RB,X,NYG,2,REG,10.0",
    )


def test_ingest_damps_the_observed_ratio_toward_neutral():
    transport = ScriptedTransport([(200, _four_defense_two_week_rows())])
    engine = make_engine()

    run(
        ingest_preseason_sos(
            engine,
            TARGET_SEASON,
            source_season=SOURCE_SEASON,
            adapter=PreseasonSosAdapter(transport=transport),
        )
    )

    rows = {r.defense: r for r in run(_stored_rows(engine)) if r.position == "RB"}
    assert rows["ARI"].observed_ratio == 1.0
    assert rows["SF"].observed_ratio == 2.0
    # damped = 1 + DAMPING * (observed - 1) = 1.5 at the default 0.5, then
    # clamped to MULTIPLIER_CLAMP's 1.3 ceiling (same clamp C2 uses)
    assert rows["SF"].multiplier == 1.3
    assert rows["SF"].multiplier < 2.0  # damping (+ clamp) pulled it toward neutral
    assert rows["SF"].rank == 1  # softest (highest multiplier) ranks first
    assert rows["ARI"].rank == 2
    assert rows["SF"].games_sampled == 2


def test_ingest_below_min_defenses_sampled_produces_no_rows():
    # only one defense sampled all season -- MIN_DEFENSES_SAMPLED (4) unmet
    transport = ScriptedTransport(
        [(200, stats_csv("A,RB,X,ARI,1,REG,10.0"))]
    )
    engine = make_engine()

    summary = run(
        ingest_preseason_sos(
            engine,
            TARGET_SEASON,
            source_season=SOURCE_SEASON,
            adapter=PreseasonSosAdapter(transport=transport),
        )
    )

    assert summary["positions"]["RB"] == 0
    assert run(_stored_rows(engine)) == []


def test_ingest_defaults_source_season_to_target_minus_one():
    transport = ScriptedTransport([(200, _four_defense_two_week_rows())])
    engine = make_engine()

    summary = run(
        ingest_preseason_sos(
            engine, TARGET_SEASON, adapter=PreseasonSosAdapter(transport=transport)
        )
    )

    assert summary["source_season"] == TARGET_SEASON - 1


def test_ingest_replaces_not_duplicates_on_rerun():
    engine = make_engine()

    def transport():
        return ScriptedTransport([(200, _four_defense_two_week_rows())])

    run(
        ingest_preseason_sos(
            engine,
            TARGET_SEASON,
            source_season=SOURCE_SEASON,
            adapter=PreseasonSosAdapter(transport=transport()),
        )
    )
    run(
        ingest_preseason_sos(
            engine,
            TARGET_SEASON,
            source_season=SOURCE_SEASON,
            adapter=PreseasonSosAdapter(transport=transport()),
        )
    )

    rows = [r for r in run(_stored_rows(engine)) if r.position == "RB"]
    assert len(rows) == 4  # ARI + SF + DAL + NYG, not 8


def test_ingest_fetch_failure_writes_nothing_and_reports_error():
    transport = ScriptedTransport([(500, "server error")])
    engine = make_engine()

    summary = run(
        ingest_preseason_sos(
            engine,
            TARGET_SEASON,
            source_season=SOURCE_SEASON,
            adapter=PreseasonSosAdapter(transport=transport),
        )
    )

    assert "error" in summary
    assert run(_stored_rows(engine)) == []


# --- read path ---------------------------------------------------------------------


def test_preseason_defense_strength_empty_carries_a_note():
    engine = make_engine()

    strength = run(preseason_defense_strength(engine, TARGET_SEASON))

    assert strength["positions"] == {}
    assert strength["note"] is not None


def test_preseason_sos_for_defaults_to_neutral_when_unfetched():
    entry = preseason_sos_for({"positions": {}}, "RB", "SF")
    assert entry["multiplier"] == 1.0
    assert entry["games_sampled"] == 0
    assert entry["rank"] is None


def test_preseason_defense_strength_reads_back_ingested_rows():
    transport = ScriptedTransport([(200, _four_defense_two_week_rows())])
    engine = make_engine()
    run(
        ingest_preseason_sos(
            engine,
            TARGET_SEASON,
            source_season=SOURCE_SEASON,
            adapter=PreseasonSosAdapter(transport=transport),
        )
    )

    strength = run(preseason_defense_strength(engine, TARGET_SEASON))
    assert strength["source_season"] == SOURCE_SEASON
    entry = preseason_sos_for(strength, "RB", "SF")
    assert entry["rank"] == 1


def test_cached_only_read_module_never_imports_data_sources():
    import ast
    import inspect

    from models import preseason_sos as preseason_sos_models

    tree = ast.parse(inspect.getsource(preseason_sos_models))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [node.module or ""]
        else:
            continue
        for name in imported:
            assert not name.startswith("data_sources")
