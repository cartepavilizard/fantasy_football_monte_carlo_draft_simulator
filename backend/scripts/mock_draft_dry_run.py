# -*- coding: utf-8 -*-
"""
MOCK-DRAFT DRY RUN (outstanding operational item 6)

An end-to-end rehearsal against whatever board the master leagues
actually hold right now. The point is not to test units -- the suite does
that -- but to answer the question no unit test can: does a whole draft
run to completion on the CURRENT data, and does everything the user will
look at on draft night produce sane output?

Exercises, per league:
  - a full simulated draft, all rounds, every team
  - the resulting rosters (no empty starters, no duplicate picks)
  - the scarcity call at several draft states
  - the value_over_wait recommendation, including whether H12's
    uncertainty hedge is actually firing on the live board
  - randomized_points() draws against the adopted position_max_points

Read-only: every league is deep-copied first. Nothing is written to Mongo.
"""
import asyncio
import random
import sys
from collections import Counter

BACKEND = r"C:\fantasy_football_monte_carlo_draft_simulator\backend"
sys.path.insert(0, BACKEND)

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from odmantic import AIOEngine  # noqa: E402

import app as app_module  # noqa: E402
from models import value_over_wait as vow  # noqa: E402
from models.config import DRAFT_YEAR  # noqa: E402
from models.team import League  # noqa: E402

YEAR = str(DRAFT_YEAR)
MASTERS = ["6a68d1a529cc6c50523f2022", "6a5aa7e98a72088bca98c230"]

engine = AIOEngine(
    database="fantasy-football",
    client=AsyncIOMotorClient("mongodb://localhost:27017"),
)

problems = []


def check(cond, msg):
    print(("  ok:   " if cond else "  FAIL: ") + msg)
    if not cond:
        problems.append(msg)


async def main():
    leagues = {str(lg.id): lg for lg in await engine.find(League)}
    for league_id in MASTERS:
        base = leagues.get(league_id)
        if base is None:
            check(False, f"master league {league_id} missing")
            continue
        print(f"\n{'=' * 74}\n{base.name}  ({len(base.teams)} teams, "
              f"{base.round_size} rounds, {len(base.players.players)} players)"
              f"\n{'=' * 74}")

        conf = Counter(p.tier_confidence for p in base.players.players)
        print(f"  board tier_confidence: {dict(conf)}")
        check(
            conf.get("low", 0) > 0 and conf.get("high", 0) > 0,
            f"the board carries a real confidence spread {dict(conf)}",
        )

        # ---- 1. a full draft, start to finish ----
        random.seed(20260802)
        lg = base.model_copy(deep=True)
        model = app_module.fit_logistic_regression_model(
            lg.logistic_regression_variables
        )
        total = lg.round_size * len(lg.teams)
        try:
            app_module.simulate_draft(lg, model)
            crashed = None
        except Exception as exc:  # noqa: BLE001
            crashed = f"{type(exc).__name__}: {exc}"
        check(crashed is None, f"a full {total}-pick draft runs to completion "
                               f"({crashed or 'no errors'})")
        if crashed:
            continue

        drafted = [p for p in lg.players.players if p.drafted]
        check(
            len(drafted) == total,
            f"exactly {total} players were drafted (got {len(drafted)})",
        )
        names = [p.name for p in drafted]
        check(
            len(names) == len(set(names)),
            f"no player was drafted twice ({len(names) - len(set(names))} dupes)",
        )

        # ---- 2. every team fielded a legal starting lineup ----
        empty = []
        for t in lg.teams:
            for position in ["qb", "rb", "wr", "te", "dst", "k"]:
                if not getattr(t, position):
                    empty.append(f"{t.name}:{position}")
        check(
            not empty,
            f"every team filled every starting position "
            f"({len(empty)} gaps{': ' + ', '.join(empty[:5]) if empty else ''})",
        )

        # ---- 3. randomized_points respects the adopted ceilings ----
        random.seed(7)
        maxes = base.position_max_points
        over = []
        for p in base.players.players[:400]:
            drawn = p.randomized_points(
                distributions=base.position_tier_distributions,
                max_points=maxes,
                year=YEAR,
            )
            ceiling = maxes.model_dump()[p.position] * 1.1 + 1
            if drawn.randomized_points > ceiling:
                over.append((p.name, drawn.randomized_points, ceiling))
        check(
            not over,
            f"randomized draws stay under the adopted per-position ceilings "
            f"({len(over)} breaches{': ' + str(over[:3]) if over else ''})",
        )

        # ---- 4. the decision surfaces, at several draft states ----
        hedges = 0
        states = 0
        for target_round in (1, 4, 8, 12):
            fresh = base.model_copy(deep=True)
            picks = (target_round - 1) * len(fresh.teams)
            random.seed(11)
            for _ in range(picks):
                if not fresh.draft_order:
                    break
                name = app_module.simulate_pick(fresh, model)
                app_module.draft_player(name, fresh)
            if not fresh.draft_order:
                continue
            report = vow.cost_of_waiting_report(
                fresh, model, seed=3, max_iterations=40, year=YEAR
            )
            states += 1
            hedge = "defensible" in report["recommendation_reason"]
            hedges += 1 if hedge else 0
            print(
                f"  r{target_round:<2} -> {report['recommended_position']:3s} "
                f"{str(report['recommended_pick'])[:24]:24s} "
                f"{'[hedged] ' if hedge else '          '}"
                f"{report['recommendation_reason'][:66]}"
            )
            check(
                report["recommended_position"] is not None
                and report["recommended_pick"] is not None,
                f"round {target_round}: the engine recommends a real player",
            )
        check(states > 0, "at least one decision state was evaluated")

        # ---- 5. scarcity ----
        fresh = base.model_copy(deep=True)
        try:
            from models.scarcity import tier_breakdown

            for position in ["qb", "rb", "wr", "te"]:
                pool = [
                    p for p in getattr(fresh.players, position) if not p.drafted
                ]
                active, players, nxt, _ = tier_breakdown(pool)
                check(
                    active is not None and players,
                    f"{position}: tier data present for scarcity "
                    f"(active tier {active}, {len(players)} in it)",
                )
        except Exception as exc:  # noqa: BLE001
            check(False, f"scarcity tier breakdown raised {exc}")

        print(f"  H12 hedge fired on {hedges}/{states} evaluated states")

    print(f"\n{'=' * 74}")
    print(f"{len(problems)} problem(s)")
    for p in problems:
        print(f"  FAILED: {p}")
    sys.exit(1 if problems else 0)


asyncio.run(main())
