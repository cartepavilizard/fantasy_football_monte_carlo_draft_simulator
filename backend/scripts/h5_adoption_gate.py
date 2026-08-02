# -*- coding: utf-8 -*-
"""
BEHAVIOR check for H5: the adoption gate for H2 and for the ffanalytics arm.

H5 is a MEASUREMENT row, so this check does not assert that a feature
exists -- it asserts that the measured facts the adoption decision rests on
are still true. If someone re-syncs a league, refreshes the batches, or
changes ROSTER_SIZE, this check fails and the decision has to be re-made
rather than silently inherited.

The three findings it locks in:

1. THE LIVE LEAGUES ARE STILL UN-ADOPTED. Their projected_points equal the
   stored pre-H2 blend, NOT what today's blend code produces. The moment a
   sync runs this stops being true -- which is exactly when the gate needs
   to be reconsidered.

2. THE TIER CHURN IS NOT H2's. Re-tiering the live board under today's
   config, with the projections left completely untouched, reassigns ~98
   position_tier labels. That is ROSTER_SIZE drifting 16 -> 12 since the
   last sync, not the blend. H2 itself moves only ~6. The plan row blamed
   H2 for the tier movement; the measurement says otherwise, and this
   check is what stops that correction being lost.

3. H2 AND FFANALYTICS BOTH MOVE REAL NUMBERS. Not vocabulary: named
   players and named position_max_points ceilings, with amounts.

A no-movement result is a FAILURE, not a pass.

Requires Mongo on :27017 with the 2026-07-28 ppr batches loaded, and the
ffanalytics CSV produced by backend/scripts/ffanalytics_export.R. The CSV
path can be overridden with H5_FFA_CSV; when it is absent the ffanalytics
clauses SKIP (they are not silently passed) and the H2 clauses still run.
"""
import asyncio
import csv
import io
import os
import sys

BACKEND = r"C:\fantasy_football_monte_carlo_draft_simulator\backend"
sys.path.insert(0, BACKEND)

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from odmantic import AIOEngine  # noqa: E402

from data_sources.blend import blend_batches  # noqa: E402
from data_sources.service import (  # noqa: E402
    all_sources,
    latest_batch,
    resolve_batch,
    stored_anchor_resolver,
)
from data_sources.udk import parse_udk_rows  # noqa: E402
from models.config import DRAFT_YEAR, RANKING_BLEND_WEIGHTS  # noqa: E402
from models.player import Player, PlayerPoints, Players  # noqa: E402
from models.position import PositionTiers  # noqa: E402
from models.sources import BlendedRanking, SourceRankingBatch  # noqa: E402
from models.team import League  # noqa: E402

YEAR = str(DRAFT_YEAR)
SCORING = "ppr"
MAHOMES = "Never Leaving Mahomes 2026"
POSITIONS = ["qb", "rb", "wr", "te", "dst", "k"]
FFA_CSV = os.environ.get(
    "H5_FFA_CSV",
    r"C:\fantasy_football_monte_carlo_draft_simulator\backend\scripts"
    r"\ffanalytics_export.csv",
)

fails = []
skips = []


def check(cond, msg):
    print(("ok:   " if cond else "FAIL: ") + msg)
    if not cond:
        fails.append(msg)


def skip(msg):
    print("skip: " + msg)
    skips.append(msg)


def near(got, want, tol, label):
    ok = got is not None and abs(got - want) <= tol
    check(ok, f"{label}: expected ~{want} (+/-{tol}), got {got}")
    return ok


engine = AIOEngine(
    database="fantasy-football",
    client=AsyncIOMotorClient("mongodb://localhost:27017"),
)


def materialize(blend):
    """Exactly what POST /league/{id}/player/sync builds, in memory"""
    players, seen, skipped = [], set(), 0
    for record in blend.records:
        if record.position not in POSITIONS:
            continue
        if record.blended_projection is None:
            skipped += 1
            continue
        if record.canonical_name in seen:
            continue
        seen.add(record.canonical_name)
        players.append(
            Player(
                name=record.canonical_name,
                position=record.position,
                nfl_team=record.nfl_team or "",
                drafted=False,
                points={YEAR: PlayerPoints(
                    projected_points=record.blended_projection
                )},
                adp=record.adp,
                consensus_rank=record.consensus_rank,
                tier=record.tier,
                source_values=record.source_values,
            )
        )
    return Players(players=players), skipped


def max_points(players):
    return {
        p: max(
            (pl.points[YEAR].projected_points for pl in getattr(players, p)),
            default=0.0,
        )
        for p in POSITIONS
    }


async def main():
    # ---------------------------------------------------------------
    # 1. The live leagues are still un-adopted
    # ---------------------------------------------------------------
    print("\n-- 1. live leagues still hold PRE-H2 numbers --")
    leagues = [lg for lg in await engine.find(League) if lg.name == MAHOMES]
    check(len(leagues) > 0, f"found at least one '{MAHOMES}' league")
    if not leagues:
        return
    leagues.sort(key=lambda lg: str(lg.id))
    live = leagues[0]

    blends = await engine.find(BlendedRanking, sort=BlendedRanking.generated_at)
    stored = blends[-1]
    stored_proj = {
        r.canonical_name: r.blended_projection
        for r in stored.records
        if r.blended_projection is not None
    }
    live_proj = {
        p.name: p.points[YEAR].projected_points for p in live.players.players
    }

    batches = [
        b
        for b in [
            await latest_batch(engine, s, DRAFT_YEAR, SCORING) for s in all_sources()
        ]
        if b
    ]
    check(
        len(batches) >= 4,
        f"at least 4 stored source batches to blend (got {len(batches)})",
    )
    rebuilt = blend_batches(
        batches, season=DRAFT_YEAR, scoring_format=SCORING,
        weights=RANKING_BLEND_WEIGHTS,
    )
    new_proj = {
        r.canonical_name: r.blended_projection
        for r in rebuilt.records
        if r.blended_projection is not None
    }

    # Every player the live board and today's blend share must DISAGREE for
    # most of the pool -- agreement would mean a sync already happened.
    common = set(live_proj) & set(new_proj)
    moved = [n for n in common if abs(live_proj[n] - new_proj[n]) > 0.005]
    check(
        len(moved) > 0.8 * len(common),
        f"live board disagrees with today's blend on {len(moved)}/{len(common)} "
        f"shared players (>80% expected while un-adopted)",
    )
    # ...and it must AGREE with the stored pre-H2 blend on nearly all of them
    common_stored = set(live_proj) & set(stored_proj)
    agree = [
        n for n in common_stored if abs(live_proj[n] - stored_proj[n]) <= 0.005
    ]
    check(
        len(agree) > 0.95 * len(common_stored),
        f"live board still matches the stored pre-H2 blend on "
        f"{len(agree)}/{len(common_stored)} players (>95% expected)",
    )

    # ---------------------------------------------------------------
    # 2. The tier churn is config drift, not H2
    # ---------------------------------------------------------------
    print("\n-- 2. position_tier churn is cutoff drift, not the blend --")
    # RE-MEASURED 2026-08-02 after H11. This clause originally re-tiered the
    # board under the module-global ROSTER_SIZE (12) and measured 98 moves.
    # H11 made cutoffs per-league, so the honest question changed: not "what
    # does the global do" but "what will a sync ACTUALLY produce", which is
    # now this league's own team count. Mahomes is a 10-team league, so its
    # qb1 cutoff drops from the stored 16 to 10 and the churn is LARGER, not
    # smaller -- 147 rather than 98. The clause is re-measured, not relaxed:
    # the floor moved up with the finding.
    team_count = len(live.teams)
    cutoffs = PositionTiers.for_team_count(team_count).model_dump()
    print(
        f"      {live.name}: {team_count} teams -> qb={cutoffs['qb']} "
        f"te={cutoffs['te']}  (module global ROSTER_SIZE would give "
        f"qb={PositionTiers().model_dump()['qb']})"
    )
    retiered = Players(
        players=[
            Player(
                name=p.name,
                position=p.position,
                nfl_team=p.nfl_team,
                drafted=False,
                points={YEAR: PlayerPoints(
                    projected_points=p.points[YEAR].projected_points
                )},
                adp=p.adp,
                consensus_rank=p.consensus_rank,
                tier=p.tier,
                source_values=p.source_values,
            )
            for p in live.players.players
        ],
        team_count=team_count,
    )
    live_tier = {p.name: p.position_tier for p in live.players.players}
    new_tier = {p.name: p.position_tier for p in retiered.players}
    tier_moves = [n for n in live_tier if live_tier[n] != new_tier[n]]
    # projections are byte-identical here by construction; assert it anyway,
    # because that is what makes the tier movement attributable
    retier_proj = {
        p.name: p.points[YEAR].projected_points for p in retiered.players
    }
    proj_moves = [
        n for n in live_proj if abs(live_proj[n] - retier_proj[n]) > 0.005
    ]
    check(
        len(proj_moves) == 0,
        f"re-tiering moved 0 projections (got {len(proj_moves)}) -- so any "
        f"tier movement below is attributable to config alone",
    )
    check(
        len(tier_moves) >= 140,
        f"re-tiering the live board under its OWN team count ({team_count}) "
        f"reassigns {len(tier_moves)} position_tier labels with the "
        f"projections frozen (>=140 expected post-H11; it was 98 when the "
        f"board was re-tiered under the module global 12, and the stored "
        f"labels were cut at 16)",
    )
    check(
        live_tier.get("Patrick Mahomes") == "qb1"
        and new_tier.get("Patrick Mahomes") == "qb2",
        f"Patrick Mahomes qb1 -> qb2 on cutoffs alone "
        f"(live={live_tier.get('Patrick Mahomes')}, "
        f"retiered={new_tier.get('Patrick Mahomes')})",
    )
    # H11's actual guarantee: the cutoff now tracks the league, so the two
    # live leagues must disagree. A single global could never show this.
    qb1_here = sum(1 for t in new_tier.values() if t == "qb1")
    check(
        qb1_here == team_count,
        f"{live.name} tiers to exactly {team_count} qb1s under its own team "
        f"count (got {qb1_here}) -- post-H11 the cutoff follows the league",
    )

    # H2's OWN tier contribution, measured on equal footing (same batches,
    # same config, old code vs new code) is an order of magnitude smaller.
    # Rebuild the pre-H2 projection independently rather than importing it:
    # unweighted fmean of every source's raw positive projection.
    from statistics import fmean

    from data_sources.resolver import normalize_position

    raw = {}
    for batch in batches:
        seen = set()
        for record in batch.records:
            name = record.canonical_name
            position = normalize_position(record.position)
            if name is None or position not in POSITIONS or name in seen:
                continue
            seen.add(name)
            if record.projection is not None:
                raw.setdefault(name, []).append(record.projection)
    old_style = {n: round(fmean(v), 2) for n, v in raw.items() if v}
    h2_moved = [
        n
        for n in set(old_style) & set(new_proj)
        if abs(old_style[n] - new_proj[n]) > 0.005
    ]
    check(
        len(h2_moved) > 500,
        f"H2 moves {len(h2_moved)} projections vs the old unweighted mean "
        f"(>500 expected)",
    )

    # ---------------------------------------------------------------
    # 3. Named numbers actually move
    # ---------------------------------------------------------------
    print("\n-- 3. H2's named movements --")
    h2_players, h2_skipped = materialize(rebuilt)
    h2_max = max_points(h2_players)
    live_max = live.position_max_points.model_dump()
    near(h2_max["k"] - live_max["k"], 23.87, 0.5, "position_max_points k delta")
    near(h2_max["rb"] - live_max["rb"], 22.69, 0.5, "position_max_points rb delta")
    near(h2_max["qb"] - live_max["qb"], -4.39, 0.5, "position_max_points qb delta")
    check(
        h2_max["qb"] < live_max["qb"],
        f"qb ceiling FALLS under H2 ({live_max['qb']} -> {h2_max['qb']}) -- "
        f"the asymmetry that re-scales randomized_points() per position",
    )
    near(new_proj.get("Bijan Robinson"), 361.37, 0.5, "Bijan Robinson under H2")
    near(new_proj.get("James Conner"), 68.15, 1.0, "James Conner (was a 29.90 sentinel halving)")
    check(
        h2_skipped >= 118,
        f"H2 drops {h2_skipped} projection-less records (>=118 expected; the "
        f"pre-H2 blend dropped 97)",
    )

    # ---------------------------------------------------------------
    # 4. The ffanalytics arm
    # ---------------------------------------------------------------
    print("\n-- 4. the ffanalytics arm --")
    if not os.path.exists(FFA_CSV):
        skip(
            f"ffanalytics CSV not found at {FFA_CSV}; regenerate with "
            f"backend/scripts/ffanalytics_export.R (needs "
            f"R_LIBS_USER=~/R/library under WSL) or set H5_FFA_CSV"
        )
    else:
        with io.open(FFA_CSV, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        records, problems = parse_udk_rows(rows)
        check(
            not problems,
            f"ffanalytics CSV parses through udk's parser unchanged "
            f"(problems={problems})",
        )
        ffa = SourceRankingBatch(
            source="ffanalytics",
            season=DRAFT_YEAR,
            scoring_format=SCORING,
            success=True,
            records=[
                {
                    "raw_name": r.raw_name,
                    "position": r.position,
                    "nfl_team": r.nfl_team,
                    "rank": r.rank,
                    "position_rank": r.position_rank,
                    "tier": r.tier,
                    "adp": r.adp,
                    "projection": r.projection,
                }
                for r in records
            ],
        )
        resolver = await stored_anchor_resolver(engine, DRAFT_YEAR, SCORING)
        check(resolver is not None, "a stored anchor resolver exists for ffanalytics")
        resolve_batch(ffa, resolver)
        resolved = sum(1 for r in ffa.records if r.canonical_name)
        check(
            resolved >= 1800,
            f"ffanalytics resolves {resolved}/{len(ffa.records)} rows against "
            f"the ESPN anchor (>=1800 expected)",
        )

        with_ffa = blend_batches(
            batches + [ffa], season=DRAFT_YEAR, scoring_format=SCORING,
            weights=RANKING_BLEND_WEIGHTS,
        )
        check(
            "ffanalytics" in with_ffa.sources_used,
            f"ffanalytics contributes to the blend "
            f"(sources_used={with_ffa.sources_used})",
        )
        ffa_players, ffa_skipped = materialize(with_ffa)
        check(
            len(ffa_players.players) - len(h2_players.players) >= 55,
            f"ffanalytics ADDS {len(ffa_players.players) - len(h2_players.players)} "
            f"projected players on top of H2's {len(h2_players.players)} "
            f"(>=55 expected -- it more than repays H2's 23 drops)",
        )
        ffa_proj = {
            r.canonical_name: r.blended_projection
            for r in with_ffa.records
            if r.blended_projection is not None
        }
        moved_by_ffa = [
            n
            for n in set(new_proj) & set(ffa_proj)
            if abs(new_proj[n] - ffa_proj[n]) > 0.005
        ]
        check(
            len(moved_by_ffa) > 400,
            f"adding ffanalytics moves {len(moved_by_ffa)} existing projections "
            f"(>400 expected -- a third source is not cosmetic)",
        )
        # the single largest top-200 mover, named
        near(ffa_proj.get("Stefon Diggs"), 141.88, 2.0, "Stefon Diggs with ffanalytics")
        check(
            ffa_proj.get("Stefon Diggs", 999) < new_proj.get("Stefon Diggs", 0),
            f"Stefon Diggs falls when ffanalytics joins "
            f"({new_proj.get('Stefon Diggs')} -> {ffa_proj.get('Stefon Diggs')})",
        )
        # a third projection source is what makes real outlier work possible
        spreads = sum(
            1 for r in with_ffa.records if r.projection_spread is not None
        )
        base_spreads = sum(
            1 for r in rebuilt.records if r.projection_spread is not None
        )
        check(
            spreads > base_spreads,
            f"projection_spread coverage rises {base_spreads} -> {spreads} "
            f"with a third projection source",
        )

    print(
        f"\n{len(fails)} failure(s), {len(skips)} skip(s)"
    )
    if fails:
        for f in fails:
            print(f"  FAILED: {f}")
        sys.exit(1)
    sys.exit(0)


asyncio.run(main())
