# -*- coding: utf-8 -*-
"""
Blend math: per-source positional z-scores, weighted averaging, and the
guarantees the sync endpoint relies on (normalized positions, projections
averaged, unresolved records excluded).
"""
from pytest import approx

import data_sources.blend as blend_module
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


# --- blended_projection rescale contract (cross-source SCALE) ---------------
#
# The rescale fit is per (source, position): anchor_proj / source_proj is
# gathered over the overlap set, and the MEDIAN (with at least
# RESCALE_MIN_OVERLAP pairs and both projections >= RESCALE_MIN_PROJECTION)
# becomes the factor that maps the non-anchor source onto the anchor's
# point scale before the weighted average is taken.


def _n_overlapping_rbs(n, espn_proj, sleeper_ratio):
    """Build n RBs that ESPN and sleeper BOTH project: ESPN gets `espn_proj`
    each, sleeper gets `espn_proj * sleeper_ratio` each. Projections stay
    above RESCALE_MIN_PROJECTION (10.0) so every pair counts toward the
    rescale overlap set. ADP is added so the per-source z-score group has a
    metric to blend on."""
    espn_records = [
        record(f"RB {i}", "RB", projection=espn_proj, adp=float(i + 1))
        for i in range(n)
    ]
    sleeper_records = [
        record(
            f"RB {i}",
            "RB",
            projection=espn_proj * sleeper_ratio,
            adp=float(i + 1),
        )
        for i in range(n)
    ]
    return [
        batch("espn", espn_records),
        batch("sleeper", sleeper_records),
    ]


def test_rescale_factor_scales_non_anchor_onto_anchor():
    # 12 overlapping RBs, each with espn=200.0 and sleeper=160.0.
    # anchor (espn) / sleeper ratio = 1.25 for every player, median 1.25.
    # Rescaled sleeper contribution = 160.0 * 1.25 = 200.0.
    # Equal weights -> blended_projection = mean(200.0, 200.0) = 200.0.
    # Without rescale the answer would be mean(200.0, 160.0) = 180.0.
    batches = _n_overlapping_rbs(12, espn_proj=200.0, sleeper_ratio=0.8)
    blend = blend_batches(batches, season=2024, scoring_format="ppr")
    by_name = {r.canonical_name: r.blended_projection for r in blend.records}
    for name, proj in by_name.items():
        assert proj == approx(200.0, abs=1e-6)


def test_below_rescale_min_overlap_leaves_factor_at_one():
    # 9 overlapping RBs is below RESCALE_MIN_OVERLAP (10): factor stays 1.0,
    # so blended_projection is the plain mean of the raw projections.
    n = blend_module.RESCALE_MIN_OVERLAP - 1
    batches = _n_overlapping_rbs(n, espn_proj=200.0, sleeper_ratio=0.8)
    blend = blend_batches(batches, season=2024, scoring_format="ppr")
    by_name = {r.canonical_name: r.blended_projection for r in blend.records}
    for name, proj in by_name.items():
        assert proj == approx((200.0 + 160.0) / 2, abs=1e-6)


def test_nonpositive_projections_are_dropped_not_averaged():
    # James Conner case (sleeper 59.80 / espn 0.00 sentinel): the espn 0.0
    # is dropped on insert, so blended_projection should be the surviving
    # sleeper projection (59.80), NOT mean(59.80, 0.00) = 29.90.
    espn = batch(
        "espn",
        [record("James Conner", "RB", projection=0.0, adp=80.0)],
    )
    sleeper = batch(
        "sleeper",
        [record("James Conner", "RB", projection=59.80, adp=82.0)],
    )
    blend = blend_batches([espn, sleeper], season=2024, scoring_format="ppr")
    conner = next(r for r in blend.records if r.canonical_name == "James Conner")
    assert conner.blended_projection == approx(59.80, abs=1e-6)
    # A single surviving source -> no spread to compute -> None (not 0.0)
    assert conner.projection_spread is None

    # A sleeper negative season total is dropped the same way.
    espn = batch(
        "espn",
        [record("Weird Year Guy", "RB", projection=12.0, adp=80.0)],
    )
    sleeper = batch(
        "sleeper",
        [record("Weird Year Guy", "RB", projection=-5.0, adp=82.0)],
    )
    blend = blend_batches([espn, sleeper], season=2024, scoring_format="ppr")
    weird = next(r for r in blend.records if r.canonical_name == "Weird Year Guy")
    assert weird.blended_projection == approx(12.0, abs=1e-6)
    assert weird.projection_spread is None


def test_weights_map_changes_blended_projection():
    # The weights map is the documented knob for blended_projection: with
    # two sources whose RESCALED contributions genuinely disagree, shifting
    # the weight between them must move the projection. Build that case
    # directly — one "disagreement" player whose per-player ratio is off
    # the median that fixes the rescale factor, padded by enough same-ratio
    # players to clear RESCALE_MIN_OVERLAP.
    pad_espn = [
        record(f"Pad RB {i}", "RB", projection=200.0, adp=float(10 + i))
        for i in range(11)
    ]
    pad_sleeper = [
        record(f"Pad RB {i}", "RB", projection=200.0, adp=float(10 + i))
        for i in range(11)
    ]
    espn = batch(
        "espn",
        [record("Disagree Guy", "RB", projection=300.0, adp=1.0)] + pad_espn,
    )
    sleeper = batch(
        "sleeper",
        [record("Disagree Guy", "RB", projection=150.0, adp=2.0)] + pad_sleeper,
    )
    batches = [espn, sleeper]
    # The 11 padding players have ratio 1.0 -> median factor 1.0 (12 pairs
    # total clears RESCALE_MIN_OVERLAP). Disagree Guy's rescaled
    # contributions are espn=300.0 and sleeper=150.0*1.0=150.0, which differ.
    equal = blend_batches(batches, season=2024, scoring_format="ppr")
    heavy_espn = blend_batches(
        batches,
        season=2024,
        scoring_format="ppr",
        weights={"espn": 1.0, "sleeper": 0.0},
    )
    heavy_sleeper = blend_batches(
        batches,
        season=2024,
        scoring_format="ppr",
        weights={"espn": 0.0, "sleeper": 1.0},
    )
    eq = next(r for r in equal.records if r.canonical_name == "Disagree Guy").blended_projection
    he = next(r for r in heavy_espn.records if r.canonical_name == "Disagree Guy").blended_projection
    hs = next(r for r in heavy_sleeper.records if r.canonical_name == "Disagree Guy").blended_projection
    # Equal weights -> mean(300.0, 150.0) = 225.0. Weighting only espn ->
    # the raw anchor value 300.0 (no rescale on the anchor). Weighting only
    # sleeper -> the rescaled sleeper value 150.0. All three differ.
    assert eq == approx(225.0, abs=1e-6)
    assert he == approx(300.0, abs=1e-6)
    assert hs == approx(150.0, abs=1e-6)
    assert he != hs


def test_projection_spread_none_for_one_source_positive_for_two():
    # One projection source -> spread is None (not 0.0).
    single = batch(
        "espn",
        [record("Solo Back", "RB", projection=200.0, adp=1.0)],
    )
    blend = blend_batches([single], season=2024, scoring_format="ppr")
    solo = next(r for r in blend.records if r.canonical_name == "Solo Back")
    assert solo.projection_spread is None

    # Two sources that genuinely disagree AFTER rescale -> positive spread.
    # Build a fixture with one "disagreement" player whose per-player ratio
    # is off the median that fixes the rescale factor, padded by enough
    # same-ratio players to clear RESCALE_MIN_OVERLAP.
    pad_espn = [
        record(f"Pad RB {i}", "RB", projection=200.0, adp=float(10 + i))
        for i in range(11)
    ]
    pad_sleeper = [
        record(f"Pad RB {i}", "RB", projection=200.0, adp=float(10 + i))
        for i in range(11)
    ]
    espn = batch(
        "espn",
        [record("Disagree Guy", "RB", projection=300.0, adp=1.0)] + pad_espn,
    )
    sleeper = batch(
        "sleeper",
        [record("Disagree Guy", "RB", projection=150.0, adp=2.0)] + pad_sleeper,
    )
    blend = blend_batches([espn, sleeper], season=2024, scoring_format="ppr")
    guy = next(r for r in blend.records if r.canonical_name == "Disagree Guy")
    # The 11 padding players have ratio 1.0 -> median factor 1.0 (12 pairs
    # total clears RESCALE_MIN_OVERLAP). Disagree Guy's rescaled
    # contributions are espn=300.0 and sleeper=150.0*1.0=150.0, which differ
    # by 150.0 — that residual disagreement is exactly what spread surfaces.
    assert guy.projection_spread == approx(150.0, abs=1e-6)
    assert guy.projection_spread > 0


def test_factors_are_computed_per_position_not_pooled():
    # Two positions with DIFFERENT anchor/source ratios. A pooled factor
    # would map one of them wrong; a per-position factor maps both right.
    # RB: espn=200, sleeper=100 -> ratio 2.0 -> rescaled sleeper = 200.0
    # K:  espn=140, sleeper=200 -> ratio 0.7 -> rescaled sleeper = 140.0
    # Equal-weight blend in both cases -> 200.0 (RB) and 140.0 (K).
    espn = batch(
        "espn",
        [record(f"RB {i}", "RB", projection=200.0, adp=float(i + 1)) for i in range(12)]
        + [record(f"K {i}", "K", projection=140.0, adp=float(200 + i)) for i in range(12)],
    )
    sleeper = batch(
        "sleeper",
        [record(f"RB {i}", "RB", projection=100.0, adp=float(i + 1)) for i in range(12)]
        + [record(f"K {i}", "K", projection=200.0, adp=float(200 + i)) for i in range(12)],
    )
    blend = blend_batches([espn, sleeper], season=2024, scoring_format="ppr")
    by_name = {r.canonical_name: r.blended_projection for r in blend.records}
    # A pooled factor (one scalar across both positions) could not land
    # both positions back on the anchor: it would have to be 2.0 (right for
    # RB, wrong for K) or 0.7 (right for K, wrong for RB), never both. The
    # per-position factors land both exactly on the anchor's scale.
    assert by_name["RB 0"] == approx(200.0, abs=1e-6)
    assert by_name["K 0"] == approx(140.0, abs=1e-6)

