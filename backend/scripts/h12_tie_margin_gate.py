# -*- coding: utf-8 -*-
"""
H12 GATE: does the uncertainty-aware tie margin actually change what the
user is told, and by how much?

The knob ships at 0.0 (inert). This decides whether it should. Note the
LIVE league boards carry no tier_confidence at all -- they were synced
before H7 -- so measuring on them would trivially show "no effect". The
board is therefore rebuilt in memory from the current blend WITH the
expert-dispersion channel and ffanalytics present, exactly as a post-H5
sync would produce it, and the sweep runs on that.

Seed-paired: the same seeds across every knob value, so RNG noise cannot
masquerade as signal. Nothing is written to Mongo.
"""
import asyncio
import csv
import io
import json
import os
import re
import sys

sys.path.insert(0, r"C:\fantasy_football_monte_carlo_draft_simulator\backend")

from motor.motor_asyncio import AsyncIOMotorClient
from odmantic import AIOEngine, ObjectId

import app as app_module
from data_sources.blend import blend_batches
from data_sources.fantasypros import FantasyProsAdapter
from data_sources.service import (
    all_sources,
    latest_batch,
    resolve_batch,
    stored_anchor_resolver,
)
from data_sources.udk import parse_udk_rows
from models import value_over_wait as vow
from models.config import DRAFT_YEAR, RANKING_BLEND_WEIGHTS
from models.player import Player, PlayerPoints, Players
from models.sources import SourceRankingBatch
from models.team import League

engine = AIOEngine(
    database="fantasy-football",
    client=AsyncIOMotorClient("mongodb://localhost:27017"),
)
YEAR = str(DRAFT_YEAR)
SCORING = "ppr"
BACKEND = r"C:\fantasy_football_monte_carlo_draft_simulator\backend"
FFA_CSV = os.path.join(BACKEND, "scripts", "ffanalytics_export.csv")
CACHE = os.path.join(BACKEND, ".data_source_cache", "fantasypros")
POSITIONS = ["qb", "rb", "wr", "te", "dst", "k"]
SEEDS = [1, 2, 3, 4, 5]
KNOBS = [0.0, 0.5, 1.0, 2.0]
ROUNDS = [1, 3, 5, 7, 9, 11]


def cached_fantasypros():
    for name in sorted(os.listdir(CACHE)):
        entry = json.load(open(os.path.join(CACHE, name), encoding="utf-8"))
        body = entry.get("text") or ""
        m = re.search(r"ecrData\s*=\s*", body)
        if m:
            data, _ = json.JSONDecoder().raw_decode(body[m.end():])
            if data.get("players"):
                return data
    return None


async def build_board():
    """The blend a post-H5/H7/H10 sync would produce, materialized"""
    batches = [
        b
        for b in [await latest_batch(engine, s, DRAFT_YEAR, SCORING) for s in all_sources()]
        if b
    ]
    # refresh fantasypros from cache so it carries expert_rank_std
    payload = cached_fantasypros()
    recs = FantasyProsAdapter()._parse_players(payload, mode="page")
    batches = [b for b in batches if b.source != "fantasypros"]
    batches.append(
        SourceRankingBatch(
            source="fantasypros", season=DRAFT_YEAR, scoring_format=SCORING,
            success=True,
            records=[
                {
                    "raw_name": r.raw_name, "canonical_name": r.raw_name,
                    "position": r.position, "nfl_team": r.nfl_team,
                    "rank": r.rank, "position_rank": r.position_rank,
                    "tier": r.tier, "expert_rank_std": r.expert_rank_std,
                }
                for r in recs
            ],
        )
    )
    # ffanalytics
    with io.open(FFA_CSV, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    frecs, _ = parse_udk_rows(rows)
    ffa = SourceRankingBatch(
        source="ffanalytics", season=DRAFT_YEAR, scoring_format=SCORING,
        success=True,
        records=[
            {
                "raw_name": r.raw_name, "position": r.position,
                "nfl_team": r.nfl_team, "rank": r.rank,
                "position_rank": r.position_rank, "tier": r.tier,
                "adp": r.adp, "projection": r.projection,
            }
            for r in frecs
        ],
    )
    resolver = await stored_anchor_resolver(engine, DRAFT_YEAR, SCORING)
    resolve_batch(ffa, resolver)
    batches.append(ffa)

    blend = blend_batches(
        batches, season=DRAFT_YEAR, scoring_format=SCORING,
        weights=RANKING_BLEND_WEIGHTS,
    )
    return blend


async def main():
    blend = await build_board()
    conf = {}
    players = []
    seen = set()
    for r in blend.records:
        if r.position not in POSITIONS or r.blended_projection is None:
            continue
        if r.canonical_name in seen:
            continue
        seen.add(r.canonical_name)
        conf[r.tier_confidence] = conf.get(r.tier_confidence, 0) + 1
        players.append(
            Player(
                name=r.canonical_name, position=r.position,
                nfl_team=r.nfl_team or "", drafted=False,
                points={YEAR: PlayerPoints(projected_points=r.blended_projection)},
                adp=r.adp, consensus_rank=r.consensus_rank, tier=r.tier,
                source_values=r.source_values, tier_confidence=r.tier_confidence,
            )
        )
    print(f"board: {len(players)} players, tier_confidence {conf}")

    leagues = [lg for lg in await engine.find(League)
               if lg.name == "Never Leaving Mahomes 2026"]
    leagues.sort(key=lambda lg: str(lg.id))
    base_league = leagues[0]
    n_teams = len(base_league.teams)
    simulator = [i for i, t in enumerate(base_league.teams) if t.simulator][0]
    model = app_module.fit_logistic_regression_model(
        base_league.logistic_regression_variables
    )

    script = [
        p.name for p in sorted(
            [p for p in players if p.adp is not None], key=lambda p: p.adp
        )
    ]

    results = {}
    for knob in KNOBS:
        vow.UNCERTAINTY_TIE_WIDENING = knob
        lg = base_league.model_copy(deep=True)
        lg.players = Players(
            players=[p.model_copy() for p in players], team_count=n_teams
        )
        cursor = 0
        per_round = {}
        for target in ROUNDS:
            while True:
                rnd = lg.current_draft_turn // n_teams + 1
                on_clock = lg.draft_order[0] if lg.draft_order else None
                if rnd >= target and on_clock == simulator:
                    break
                if not lg.draft_order or cursor >= len(script):
                    break
                name = script[cursor]; cursor += 1
                pl = next((p for p in lg.players.players if p.name == name), None)
                if pl is None or pl.drafted:
                    continue
                app_module.draft_player(name, lg)
            per_round[target] = [
                vow.cost_of_waiting_report(lg, model, seed=s, max_iterations=100)
                for s in SEEDS
            ]
        results[knob] = per_round
    vow.UNCERTAINTY_TIE_WIDENING = 0.0

    print("\nseed-paired verdict diff vs knob=0.0 (the shipped default)")
    base = results[0.0]
    for knob in KNOBS[1:]:
        pos_f = pick_f = tie_f = 0
        detail = []
        for target in ROUNDS:
            for i, s in enumerate(SEEDS):
                b, x = base[target][i], results[knob][target][i]
                if b["recommended_position"] != x["recommended_position"]:
                    pos_f += 1
                if b["recommended_pick"] != x["recommended_pick"]:
                    pick_f += 1
                bt = "defensible" in b["recommendation_reason"]
                xt = "defensible" in x["recommendation_reason"]
                if bt != xt:
                    tie_f += 1
                    detail.append(
                        f"      r{target} seed {s}: tie-flag "
                        f"{'ON' if bt else 'off'} -> {'ON' if xt else 'off'}  "
                        f"| {x['recommendation_reason']}"
                    )
        total = len(ROUNDS) * len(SEEDS)
        print(
            f"  knob {knob}: position flips {pos_f}/{total}   "
            f"pick flips {pick_f}/{total}   TIE-FLAG changes {tie_f}/{total}"
        )
        for d in detail[:8]:
            print(d)


asyncio.run(main())
