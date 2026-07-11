# -*- coding: utf-8 -*-
"""
HANDCUFF MAP (PHASE C, TASK C7 — sourcing decided once, here)

The starter -> direct-backup map behind handcuff flags ("your RB1's
insurance is sitting on waivers") and, later, E5's blocking plays
(rivals' handcuffs worth denying).

SOURCING DECISION: a CURATED SEED TABLE in Mongo with CRUD, not
depth-chart inference. Considered:

- Depth-chart inference (scrape ESPN/OurLads depth charts, or infer
  RB2s from C4's snap shares): another fetch surface and parser to
  maintain for ~32 slowly-changing mappings; ESPN's depth charts are
  notoriously stale for exactly the ambiguous backfields where the
  answer matters; and usage inference is weakest in September (weeks
  1-3 have almost no data) — which is precisely when handcuff value
  peaks (drafts, early injuries).
- Curated table: the true mapping is fantasy-community common
  knowledge that changes maybe a dozen times a season. One review
  before the season plus rare in-season edits through the CRUD beats a
  scraper that silently rots. Handcuffs are also a JUDGMENT ("direct
  backup who inherits the role"), not a data fact — a curated row can
  encode "committee, no true handcuff" by simply not existing.

The seed below reflects END-OF-2025 depth charts (this codebase's
knowledge horizon) and MUST get a human review pass before the 2026
season — that's the deliberate cost of curation, it happens once, in
August, alongside draft prep. Rows are RB-only by design: RB is the
position where a single injury transfers a whole workload; WR/TE
committees don't handcuff. seed_handcuffs() only inserts missing
starters, so manual edits and deletions always survive re-seeding.

Later enhancement (post-C4-ingestion): flag a mapping as suspect when
the handcuff's trailing 2-week snap share exceeds the starter's — the
usage data auditing the curation, not replacing it.

--- SPEC FOR THE CHEAP HALF (flagging logic + UI) ---------------------

1. `available_handcuff_flags(engine, espn_league_id, season, week)`
   here in this module: join the map against that league's synced
   data — for each pair where the STARTER is on some team's roster
   (TeamWeekRoster, current week) and the HANDCUFF appears in the
   league's FreeAgentSnapshot, emit
     {starter_name, handcuff_name, nfl_team, starter_team_id,
      starter_injury_status, handcuff_projected_points,
      handcuff_percent_owned, priority}
   priority = "high" when starter_injury_status is questionable/
   doubtful/out (insurance is urgent), else "normal".
2. Endpoint `GET /inseason/league/{id}/handcuffs` in inseason_api
   (Mongo-only; add to both cached-only enforcement tests) returning
   the flags plus the standard freshness envelope.
3. Notifications ONLY for priority "high" (a healthy starter's
   available handcuff is a row in a table, not a push): through
   ensure_notification, kind "handcuff_available", dedupe key
   f"handcuff:{league}:{season}:w{week}:{starter_name}", body in
   insurance/opportunity framing (whose workload it protects, what
   share of touches the starter commands — never points).
4. C9 rides on this: when a flagged handcuff is a HOMER_TEAM player,
   attach models/homer.py's homer_check against the same-position
   free-agent pool (one methodology, new call site).
5. UI: flags as chips on the roster view + a handcuff panel with the
   CRUD (list/edit/delete, seed button).
----------------------------------------------------------------------
"""
import datetime
from typing import List, Optional

from odmantic import Model
from odmantic import Field as ODField
from odmantic import query


class HandcuffPair(Model):
    """One starter -> direct-backup mapping (starter_name is the key)"""

    model_config = {"collection": "handcuffs"}

    starter_name: str
    handcuff_name: str
    nfl_team: Optional[str] = None
    position: str = "RB"
    note: Optional[str] = None
    source: str = "seed"  # seed | manual
    # deletions are soft so they survive re-seeding ("this backfield is
    # a committee now" must not resurrect on the next seed pass)
    active: bool = True
    updated_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# End-of-2025 depth charts — REVIEW BEFORE THE 2026 SEASON (see module
# docstring). (starter, handcuff, team, optional note)
SEED_HANDCUFF_PAIRS = [
    ("Christian McCaffrey", "Isaac Guerendo", "SF", None),
    ("Bijan Robinson", "Tyler Allgeier", "ATL", None),
    ("Saquon Barkley", "Will Shipley", "PHI", None),
    ("Derrick Henry", "Justice Hill", "BAL", None),
    ("Kyren Williams", "Blake Corum", "LAR", None),
    ("Breece Hall", "Braelon Allen", "NYJ", None),
    ("James Cook", "Ray Davis", "BUF", None),
    ("Kenneth Walker III", "Zach Charbonnet", "SEA", None),
    ("Bucky Irving", "Rachaad White", "TB", None),
    ("Josh Jacobs", "Emanuel Wilson", "GB", None),
    ("Jonathan Taylor", "DJ Giddens", "IND", None),
    ("De'Von Achane", "Jaylen Wright", "MIA", None),
    ("Alvin Kamara", "Kendre Miller", "NO", None),
    ("Chuba Hubbard", "Rico Dowdle", "CAR", None),
    ("Isiah Pacheco", "Kareem Hunt", "KC", None),
    ("James Conner", "Trey Benson", "ARI", None),
    ("Jaylen Warren", "Kaleb Johnson", "PIT", None),
    ("Tony Pollard", "Tyjae Spears", "TEN", None),
    ("D'Andre Swift", "Kyle Monangai", "CHI", None),
    ("Aaron Jones", "Jordan Mason", "MIN", None),
    ("Quinshon Judkins", "Jerome Ford", "CLE", None),
    ("Chase Brown", "Tahj Brooks", "CIN", "verify RB2 in camp"),
    ("Travis Etienne", "Bhayshul Tuten", "JAX", "verify RB2 in camp"),
    ("Joe Mixon", "Dameon Pierce", "HOU", "verify RB2 in camp"),
    # committees deliberately absent (DET, NE, NYG, DEN, LAC, WAS, DAL):
    # no single direct backup inherits the role — encode by omission
]


async def seed_handcuffs(engine, pairs=None) -> dict:
    """
    Insert any seed pair whose starter is not already known. Existing
    rows — manual edits, repointed backups, and soft-deleted mappings
    alike — are never touched, so re-seeding is always safe.
    """
    if pairs is None:
        pairs = SEED_HANDCUFF_PAIRS
    existing = {
        pair.starter_name for pair in await engine.find(HandcuffPair)
    }  # includes inactive rows: a deletion is a curation decision too
    created = 0
    for starter, handcuff, team, note in pairs:
        if starter in existing:
            continue
        await engine.save(
            HandcuffPair(
                starter_name=starter,
                handcuff_name=handcuff,
                nfl_team=team,
                note=note,
                source="seed",
            )
        )
        created += 1
    return {"created": created, "skipped": len(pairs) - created}


async def list_handcuffs(engine) -> List[HandcuffPair]:
    return await engine.find(
        HandcuffPair,
        HandcuffPair.active == True,  # noqa: E712
        sort=query.asc(HandcuffPair.starter_name),
    )


async def upsert_handcuff(
    engine,
    starter_name: str,
    handcuff_name: str,
    nfl_team: Optional[str] = None,
    note: Optional[str] = None,
) -> HandcuffPair:
    """Create or repoint one mapping; manual edits win over seeds"""
    pair = await engine.find_one(
        HandcuffPair, HandcuffPair.starter_name == starter_name
    )
    if pair is None:
        pair = HandcuffPair(
            starter_name=starter_name, handcuff_name=handcuff_name
        )
    pair.handcuff_name = handcuff_name
    if nfl_team is not None:
        pair.nfl_team = nfl_team
    if note is not None:
        pair.note = note
    pair.source = "manual"
    pair.active = True  # editing a soft-deleted mapping revives it
    pair.updated_at = datetime.datetime.now()
    await engine.save(pair)
    return pair


async def delete_handcuff(engine, starter_name: str) -> bool:
    """Soft delete: the row stays so re-seeding cannot resurrect it"""
    pair = await engine.find_one(
        HandcuffPair, HandcuffPair.starter_name == starter_name
    )
    if pair is None or not pair.active:
        return False
    pair.active = False
    pair.source = "manual"
    pair.updated_at = datetime.datetime.now()
    await engine.save(pair)
    return True
