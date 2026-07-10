# -*- coding: utf-8 -*-
"""
Blend math: per-source positional z-scores, weighted averaging, and the
guarantees the sync endpoint relies on (normalized positions, projections
averaged, unresolved records excluded).
"""
from pytest import approx

from data_sources.blend import blend_batches
from models.sources import SourceRankingBatch, SourceRankingRecord


def record(name, position, canonical=None, **fields):
    return SourceRankingRecord(
        raw_name=name,
        canonical_name=canonical or name,
        resolution_method="exact",
        resolution_confidence=1.0,
        position=position,
        **fields,
    )


def batch(source, records, success=True):
    return SourceRankingBatch(
        source=source,
        season=2024,
        scoring_format="ppr",
        success=success,
        records=records,
    )


PROJECTION_BATCH = batch(
    "espn",
    [
        record("Alpha Back", "RB", projection=300.0, nfl_team="SF"),
        record("Bravo Back", "RB", projection=250.0),
        record("Charlie Back", "RB", projection=200.0),
    ],
)

# ADP source: lower is better. Alpha best, Bravo worst, Charlie middle.
ADP_BATCH = batch(
    "ffc",
    [
        record("Alpha Back", "RB", adp=1.0),
        record("Bravo Back", "RB", adp=3.0),
        record("Charlie Back", "RB", adp=2.0),
    ],
)

# Hand-computed z-scores:
#   espn projections (300/250/200): z = +1.2247, 0, -1.2247
#   ffc negated adp (-1/-3/-2):     z = +1.2247, -1.2247, 0
Z = 1.224744871


def test_zscores_within_position_per_source():
    blend = blend_batches([PROJECTION_BATCH], season=2024, scoring_format="ppr")
    values = {r.canonical_name: r.blended_value for r in blend.records}
    assert values["Alpha Back"] == approx(Z, abs=1e-3)
    assert values["Bravo Back"] == approx(0.0, abs=1e-3)
    assert values["Charlie Back"] == approx(-Z, abs=1e-3)


def test_equal_weight_blend_averages_source_zscores():
    blend = blend_batches(
        [PROJECTION_BATCH, ADP_BATCH], season=2024, scoring_format="ppr"
    )
    values = {r.canonical_name: r.blended_value for r in blend.records}
    assert values["Alpha Back"] == approx(Z, abs=1e-3)
    assert values["Bravo Back"] == approx(-Z / 2, abs=1e-3)
    assert values["Charlie Back"] == approx(-Z / 2, abs=1e-3)
    assert blend.sources_used == ["espn", "ffc"]
    # Records come out sorted best-first (the sync endpoint relies on this)
    assert blend.records[0].canonical_name == "Alpha Back"


def test_weights_shift_the_blend():
    blend = blend_batches(
        [PROJECTION_BATCH, ADP_BATCH],
        season=2024,
        scoring_format="ppr",
        weights={"espn": 3.0, "ffc": 1.0},
    )
    values = {r.canonical_name: r.blended_value for r in blend.records}
    assert values["Bravo Back"] == approx((3 * 0 + 1 * -Z) / 4, abs=1e-3)
    assert values["Charlie Back"] == approx((3 * -Z + 1 * 0) / 4, abs=1e-3)
    assert blend.source_weights == {"espn": 3.0, "ffc": 1.0}


def test_consensus_fields_average_across_sources():
    second_projection = batch(
        "sleeper",
        [record("Alpha Back", "RB", projection=310.0, adp=1.4, nfl_team="SF")],
    )
    blend = blend_batches(
        [PROJECTION_BATCH, ADP_BATCH, second_projection],
        season=2024,
        scoring_format="ppr",
    )
    alpha = next(r for r in blend.records if r.canonical_name == "Alpha Back")
    assert alpha.blended_projection == approx(305.0)  # mean(300, 310)
    assert alpha.adp == approx(1.2)  # mean(1.0, 1.4)
    assert alpha.nfl_team == "SF"
    assert set(alpha.source_values) == {"espn", "ffc", "sleeper"}


def test_positions_are_normalized_and_offbeat_ones_excluded():
    mixed = batch(
        "ffc",
        [
            record("Kicker One", "PK", adp=120.0),
            record("Kicker Two", "PK", adp=125.0),
            record("City Defense", "DEF", adp=130.0, canonical="Defense"),
            record("Other Defense", "DEF", adp=131.0, canonical="Defense2"),
            record("IDP Guy", "LB", adp=140.0),
        ],
    )
    blend = blend_batches([mixed], season=2024, scoring_format="ppr")
    positions = {r.canonical_name: r.position for r in blend.records}
    assert positions["Kicker One"] == "k"
    assert positions["Defense"] == "dst"
    assert "IDP Guy" not in positions  # not a simulator position


def test_unresolved_records_and_failed_batches_are_excluded():
    with_unresolved = batch(
        "espn",
        [
            record("Alpha Back", "RB", projection=300.0),
            record("Bravo Back", "RB", projection=250.0),
            SourceRankingRecord(
                raw_name="Mystery Man", position="RB", projection=999.0
            ),  # canonical_name None
        ],
    )
    failed = batch("sleeper", [], success=False)
    blend = blend_batches([with_unresolved, failed], season=2024, scoring_format="ppr")
    names = {r.canonical_name for r in blend.records}
    assert names == {"Alpha Back", "Bravo Back"}
    assert blend.sources_used == ["espn"]


def test_single_player_group_gets_flat_zscore():
    lonely = batch("espn", [record("Only QB", "QB", projection=350.0),
                            record("Second QB", "QB", projection=350.0)])
    blend = blend_batches([lonely], season=2024, scoring_format="ppr")
    # identical values -> zero spread -> z of 0, not a crash
    assert all(r.blended_value == 0.0 for r in blend.records)
