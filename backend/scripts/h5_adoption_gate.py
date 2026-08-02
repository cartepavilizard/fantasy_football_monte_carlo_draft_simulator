# -*- coding: utf-8 -*-
"""
H5/H13 ADOPTION GUARD -- formerly the pre-adoption gate.

HISTORY, because the flip matters. From 2026-07-31 to 2026-08-02 this
script was a GATE: it asserted the live leagues still held the pre-H2
numbers and measured what adopting would change, so that a projection
change could not reshuffle the board unnoticed before the draft. It was
written to fail the moment a sync ran, and on 2026-08-02 it did exactly
that -- which is how we knew the sync had landed.

The sync is now done (H13, 2026-08-02, owner-authorised, snapshot taken
first at C:\\fantasy-football-backups\\20260802T162037Z). So the job
changes from "prove nothing has been adopted yet" to "prove what was
adopted is what was measured". Every number asserted below was PREDICTED
by H5's measurement before the sync ran, and then reproduced by it.

What this now guards against: a silent re-sync from a different blend, a
regression in per-league tier cutoffs, and -- the one with teeth --
somebody syncing the mid-draft COPY leagues, which would wipe their
in-progress draft state.

Read-only. Writes nothing.
"""
import asyncio
import os
import sys

BACKEND = r"C:\fantasy_football_monte_carlo_draft_simulator\backend"
sys.path.insert(0, BACKEND)

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from odmantic import AIOEngine, query  # noqa: E402

from models.config import DRAFT_YEAR  # noqa: E402
from models.player import Player, PlayerPoints, Players  # noqa: E402
from models.position import PositionTiers  # noqa: E402
from models.sources import BlendedRanking  # noqa: E402
from models.team import League  # noqa: E402

YEAR = str(DRAFT_YEAR)
POSITIONS = ["qb", "rb", "wr", "te", "dst", "k"]
MASTERS = {
    "6a68d1a529cc6c50523f2022": ("Never Leaving Mahomes 2026", 10),
    "6a5aa7e98a72088bca98c230": ("Skunkweed 2026", 12),
}
SNAPSHOT_ROOT = r"C:\fantasy-football-backups"
# Pre-H2 values, kept as the floor the fixes must never regress below. These
# are DURABLE properties, unlike the exact projections H5 predicted -- those
# were pinned here at first and went stale on the very next
# POST /rankings/refresh, which is the wrong thing for a guard to do. Assert
# what must always be true, not what happened to be true on one afternoon.
PRE_H2_KICKER_CEILING = 143.78   # espn/sleeper scale disagreement, unrescaled
CONNER_SENTINEL_VALUE = 29.90    # espn 0.0 halving a real sleeper projection

fails = []


def check(cond, msg):
    print(("ok:   " if cond else "FAIL: ") + msg)
    if not cond:
        fails.append(msg)


def near(got, want, tol, label):
    ok = got is not None and abs(got - want) <= tol
    check(ok, f"{label}: expected ~{want} (+/-{tol}), got {got}")


engine = AIOEngine(
    database="fantasy-football",
    client=AsyncIOMotorClient("mongodb://localhost:27017"),
)


async def main():
    # ---------------------------------------------------------------
    # 1. The pre-sync snapshot still exists
    # ---------------------------------------------------------------
    print("\n-- 1. the backup that made the sync reversible --")
    snaps = (
        sorted(
            d for d in os.listdir(SNAPSHOT_ROOT)
            if os.path.exists(os.path.join(SNAPSHOT_ROOT, d, "MANIFEST.txt"))
        )
        if os.path.isdir(SNAPSHOT_ROOT)
        else []
    )
    check(
        bool(snaps),
        f"at least one verified snapshot is on disk under {SNAPSHOT_ROOT} "
        f"(found {snaps}) -- restore with "
        f"scripts/mongo_snapshot.py restore <dir> --yes",
    )

    # ---------------------------------------------------------------
    # 2. The masters hold the adopted blend
    # ---------------------------------------------------------------
    print("\n-- 2. the master leagues hold the adopted board --")
    blend = await engine.find_one(
        BlendedRanking,
        sort=(query.desc(BlendedRanking.generated_at), query.desc(BlendedRanking.id)),
    )
    check(
        "ffanalytics" in blend.sources_used,
        f"the latest blend is the five-source one ({blend.sources_used})",
    )
    proj = {
        r.canonical_name: r.blended_projection
        for r in blend.records
        if r.blended_projection is not None
    }
    check(
        len(proj) >= 700,
        f"the blend materialises {len(proj)} projected players (>=700; it was "
        f"672 pre-H2, 649 under H2 alone, ~710 with ffanalytics)",
    )
    check(
        sum(1 for r in blend.records if r.expert_rank_std is not None) > 400,
        f"H7's expert-dispersion channel is populated "
        f"({sum(1 for r in blend.records if r.expert_rank_std is not None)} "
        f"records) -- it is empty until a refresh runs after H7 shipped",
    )
    check(
        sum(1 for r in blend.records if r.tier_confidence is not None) > 500,
        f"tier_confidence is derived on "
        f"{sum(1 for r in blend.records if r.tier_confidence is not None)} "
        f"records",
    )
    # DURABLE properties of the H2 fixes -- these must hold after any refresh.
    conner = proj.get("James Conner")
    check(
        conner is not None and conner > CONNER_SENTINEL_VALUE * 1.5,
        f"James Conner is {conner}, well clear of the {CONNER_SENTINEL_VALUE} "
        f"an espn 0.0 sentinel used to halve him to -- the sentinel drop is "
        f"still working",
    )
    zeros = [
        r.canonical_name for r in blend.records
        if r.blended_projection is not None and r.blended_projection <= 0
    ]
    check(
        not zeros,
        f"no blended projection is <= 0 ({len(zeros)} found) -- non-positive "
        f"values are not data",
    )

    leagues = {str(lg.id): lg for lg in await engine.find(League)}
    for league_id, (name, teams) in MASTERS.items():
        lg = leagues.get(league_id)
        check(lg is not None, f"master league {name} ({league_id}) exists")
        if lg is None:
            continue
        check(
            not lg.copy_for_draft,
            f"{name} is still a master, not a draft copy",
        )
        check(
            len(lg.teams) == teams,
            f"{name} still has {teams} teams (got {len(lg.teams)})",
        )
        live = {
            p.name: p.points[YEAR].projected_points
            for p in lg.players.players
            if YEAR in p.points
        }
        common = set(live) & set(proj)
        stale = [n for n in common if abs(live[n] - proj[n]) > 0.005]
        check(
            not stale,
            f"{name}: every one of {len(common)} shared players matches the "
            f"latest blend ({len(stale)} stale) -- the board IS the blend",
        )
        check(
            len(live) >= 700,
            f"{name} carries {len(live)} players (>=700; 671 pre-adoption)",
        )
        mp = lg.position_max_points.model_dump()
        blend_max = {
            pos: max(
                (
                    p.points[YEAR].projected_points
                    for p in lg.players.players
                    if p.position == pos and YEAR in p.points
                ),
                default=0.0,
            )
            for pos in POSITIONS
        }
        bad = [p for p in POSITIONS if abs(mp[p] - blend_max[p]) > 0.01]
        check(
            not bad,
            f"{name} position_max_points is consistent with its own board "
            f"({'stale: ' + ', '.join(bad) if bad else 'all six'})",
        )
        check(
            mp["k"] > PRE_H2_KICKER_CEILING,
            f"{name} kicker ceiling {mp['k']} is above the pre-H2 "
            f"{PRE_H2_KICKER_CEILING} -- H2's per-position rescale is adopted "
            f"(espn projected 41% more kicker points than sleeper unrescaled)",
        )

        # ---- per-league tier cutoffs actually differ (H11's guarantee) ----
        qb1 = sum(1 for p in lg.players.players if p.position_tier == "qb1")
        check(
            qb1 == teams,
            f"{name} has exactly {teams} qb1s (got {qb1}) -- the cutoff "
            f"follows THIS league, not a global",
        )
        # re-tiering under its own count must now be a no-op
        retiered = Players(
            players=[
                Player(
                    name=p.name, position=p.position, nfl_team=p.nfl_team,
                    points={YEAR: PlayerPoints(
                        projected_points=p.points[YEAR].projected_points
                    )},
                )
                for p in lg.players.players
                if YEAR in p.points
            ],
            team_count=teams,
        )
        now = {p.name: p.position_tier for p in lg.players.players}
        again = {p.name: p.position_tier for p in retiered.players}
        drift = [n for n in now if n in again and now[n] != again[n]]
        check(
            not drift,
            f"{name}: re-tiering under its own count is a no-op "
            f"({len(drift)} would move) -- the stored labels are correct",
        )

    # the two leagues must NOT agree, which is the whole point of H11
    m = leagues.get("6a68d1a529cc6c50523f2022")
    s = leagues.get("6a5aa7e98a72088bca98c230")
    if m and s:
        mq = sum(1 for p in m.players.players if p.position_tier == "qb1")
        sq = sum(1 for p in s.players.players if p.position_tier == "qb1")
        check(
            mq != sq,
            f"the two leagues tier DIFFERENTLY ({mq} vs {sq} qb1s) on an "
            f"identical player pool -- impossible before H11",
        )
        check(
            PositionTiers().qb["1"] != mq,
            f"...and Mahomes' cutoff ({mq}) is NOT the module global "
            f"({PositionTiers().qb['1']}), proving the global is no longer "
            f"in charge",
        )

    # ---------------------------------------------------------------
    # 3. The draft copies were deliberately NOT synced
    # ---------------------------------------------------------------
    print("\n-- 3. mid-draft copies were left alone, and must stay that way --")
    copies = [lg for lg in leagues.values() if lg.copy_for_draft]
    check(len(copies) >= 10, f"the draft copies are still present ({len(copies)})")
    synced_copies = []
    for lg in copies:
        live = {
            p.name: p.points[YEAR].projected_points
            for p in lg.players.players
            if YEAR in p.points
        }
        if len(live) == 710:
            synced_copies.append(str(lg.id))
    check(
        not synced_copies,
        f"no draft copy has been re-synced ({synced_copies}) -- syncing one "
        f"replaces its player pool and WIPES its in-progress draft state; "
        f"copies are made fresh from a master when a draft starts",
    )
    mid_draft = [
        (str(lg.id), sum(1 for p in lg.players.players if p.drafted))
        for lg in copies
        if any(p.drafted for p in lg.players.players)
    ]
    check(
        mid_draft,
        f"the mid-draft copies still have their picks {mid_draft}",
    )

    print(f"\n{len(fails)} failure(s)")
    if fails:
        for f in fails:
            print(f"  FAILED: {f}")
        sys.exit(1)
    sys.exit(0)


asyncio.run(main())
