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
SNAPSHOT = r"C:\fantasy-football-backups\20260802T162037Z"
# position_max_points H5 predicted for the adopted board, before the sync ran
EXPECTED_MAX = {
    "qb": 354.70, "rb": 365.75, "wr": 341.29,
    "te": 243.61, "dst": 115.15, "k": 168.96,
}

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
    check(
        os.path.isdir(SNAPSHOT)
        and os.path.exists(os.path.join(SNAPSHOT, "MANIFEST.txt"))
        and os.path.exists(os.path.join(SNAPSHOT, "league.json")),
        f"the pre-sync snapshot is still on disk at {SNAPSHOT} "
        f"(restore with scripts/mongo_snapshot.py restore <dir> --yes)",
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
        len(proj) == 710,
        f"the blend materialises 710 projected players (got {len(proj)}) -- "
        f"649 under H2 alone, +61 from ffanalytics",
    )
    # the exact values H5 predicted before the sync
    near(proj.get("Bijan Robinson"), 365.75, 0.5, "Bijan Robinson")
    near(proj.get("Stefon Diggs"), 141.88, 0.5, "Stefon Diggs")
    # 75.78, not H2's 68.15: that was the TWO-source value. Conner is the
    # sentinel case -- espn writes 0.0 for "no projection" and it used to halve
    # him to 29.90. With the sentinel dropped and ffanalytics added he is
    # sleeper 59.8 (rescaled to 68.2 on espn's rb scale) averaged with
    # ffanalytics 82.8 (rescaled ~83.4). Three sources, so a different number.
    near(proj.get("James Conner"), 75.78, 2.0, "James Conner (was a 29.90 sentinel)")
    near(proj.get("Brandon Aubrey"), 168.96, 0.5, "Brandon Aubrey")

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
            len(live) == 710,
            f"{name} carries 710 players (got {len(live)})",
        )
        mp = lg.position_max_points.model_dump()
        bad = [
            f"{p} {mp[p]} != {EXPECTED_MAX[p]}"
            for p in POSITIONS
            if abs(mp[p] - EXPECTED_MAX[p]) > 0.5
        ]
        check(
            not bad,
            f"{name} position_max_points matches what H5 predicted "
            f"({'; '.join(bad) if bad else 'all six'})",
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
