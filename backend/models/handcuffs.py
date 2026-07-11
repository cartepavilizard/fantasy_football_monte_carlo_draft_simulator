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

FLAGGING (this module's cheap half): available_handcuff_flags() joins
the map above against one league's synced data — for each active pair
where the STARTER is on some team's roster (TeamWeekRoster, current
week) and the HANDCUFF is sitting in the league's FreeAgentSnapshot, it
emits one flag carrying the starter's injury status, the handcuff's
projected points/percent owned, and a priority. priority is "high" only
when starter_injury_status is questionable/doubtful/out — insurance
that's about to matter, not a healthy starter's spare parts sitting in
a table — else "normal".

Endpoint: GET /inseason/league/{id}/handcuffs (inseason_api.py) is
Mongo-only like every route in that router (no scrapes, no ESPN calls),
wrapped in the standard freshness envelope, and covered by both of B4's
cached-only enforcement tests.

NOTIFICATIONS: only priority "high" pages the phone — a healthy
starter's available handcuff is a row in the panel, not a push.
ensure_handcuff_notifications() raises them through ensure_notification
(B5), kind "handcuff_available", dedupe key
f"handcuff:{espn_league_id}:{season}:w{week}:{starter_name}". Copy
stays in C8's insurance/opportunity register — whose workload the
handcuff protects and how hurt the starter is — never fantasy points.

C9 (homer check): when a flagged handcuff plays for HOMER_TEAM, it gets
homer_check() run against the same-position free-agent pool, reusing
streaming.py's FreeAgentHomerCandidate adapter unchanged — one
methodology, one adapter, two in-season call sites now.

UI: flags render as chips on the roster view; a handcuff panel exposes
the CRUD above (list/edit/delete, seed button).
"""
import datetime
from typing import List, Optional, Tuple

from odmantic import Model
from odmantic import Field as ODField
from odmantic import query

from .config import HOMER_TEAM
from .homer import homer_check
from .inseason import FreeAgentSnapshot, TeamWeekRoster
from .notifications import ensure_notification
from .streaming import FreeAgentHomerCandidate

# starter_injury_status values urgent enough to make the handcuff's
# insurance value real rather than hypothetical (spec's contract)
HANDCUFF_URGENT_INJURY_STATUSES = {"questionable", "doubtful", "out"}


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


# --- flagging (C7's cheap half): join the map against synced data ------------


async def available_handcuff_flags(
    engine, espn_league_id: int, season: int, week: int
) -> List[dict]:
    """
    Every active handcuff pair where the starter is rostered in this
    league-week and the handcuff is sitting in the latest free-agent
    snapshot, per the module's flagging contract above (priority, C9
    homer check attached). Sorted by starter_name for a stable UI.
    """
    pairs = await list_handcuffs(engine)
    if not pairs:
        return []
    pair_by_starter = {pair.starter_name: pair for pair in pairs}

    rosters = await engine.find(
        TeamWeekRoster,
        (TeamWeekRoster.espn_league_id == espn_league_id)
        & (TeamWeekRoster.season == season)
        & (TeamWeekRoster.week == week),
    )
    rostered_starters = {}
    for roster in rosters:
        for entry in roster.entries:
            if entry.player_name in pair_by_starter:
                rostered_starters[entry.player_name] = (
                    roster.espn_team_id,
                    entry.injury_status,
                )
    if not rostered_starters:
        return []

    snapshot = await engine.find_one(
        FreeAgentSnapshot,
        (FreeAgentSnapshot.espn_league_id == espn_league_id)
        & (FreeAgentSnapshot.season == season)
        & (FreeAgentSnapshot.week == week),
        sort=(query.desc(FreeAgentSnapshot.synced_at), query.desc(FreeAgentSnapshot.id)),
    )
    free_agents = snapshot.entries if snapshot else []
    free_agent_by_name = {entry.player_name: entry for entry in free_agents}

    flags = []
    for starter_name, (team_id, injury_status) in rostered_starters.items():
        handcuff = free_agent_by_name.get(pair_by_starter[starter_name].handcuff_name)
        if handcuff is None:
            continue
        priority = (
            "high"
            if (injury_status or "").lower() in HANDCUFF_URGENT_INJURY_STATUSES
            else "normal"
        )
        flags.append(
            {
                "starter_name": starter_name,
                "handcuff_name": handcuff.player_name,
                "nfl_team": pair_by_starter[starter_name].nfl_team,
                "starter_team_id": team_id,
                "starter_injury_status": injury_status,
                "handcuff_projected_points": handcuff.projected_points,
                "handcuff_percent_owned": handcuff.percent_owned,
                "priority": priority,
                "homer_check": None,
            }
        )
    flags.sort(key=lambda flag: flag["starter_name"])

    # C9: any flagged handcuff who plays for HOMER_TEAM gets the neutral
    # comparison against the same-position free-agent pool
    year = str(season)
    for flag in flags:
        handcuff = free_agent_by_name[flag["handcuff_name"]]
        if (handcuff.nfl_team or "").upper() != HOMER_TEAM.upper():
            continue
        position = (handcuff.position or "").upper()
        pool = [
            FreeAgentHomerCandidate(entry, year)
            for entry in free_agents
            if (entry.position or "").upper() == position
            and entry.player_name != handcuff.player_name
        ]
        check = homer_check(
            FreeAgentHomerCandidate(handcuff, year), pool, pick_number=None, year=year
        )
        flag["homer_check"] = check.model_dump() if check else None

    return flags


def _handcuff_notification_copy(flag: dict, week: int) -> Tuple[str, str]:
    """Insurance/opportunity framing (whose workload it protects, how
    hurt the starter is) — deliberately never fantasy points"""
    starter = flag["starter_name"]
    handcuff = flag["handcuff_name"]
    status = flag["starter_injury_status"] or "questionable"
    team = f" ({flag['nfl_team']})" if flag["nfl_team"] else ""
    owned = flag["handcuff_percent_owned"]
    owned_phrase = f"{owned:.0f}% owned" if owned is not None else "widely available"

    title = f"{handcuff} available: {starter} is {status}"
    body = (
        f"{starter}{team} is {status} for week {week}. {handcuff} is the "
        f"direct backup who inherits {starter}'s workload if he sits, and "
        f"is sitting on waivers ({owned_phrase}) — insurance worth grabbing "
        "before it isn't."
    )
    return title, body


async def ensure_handcuff_notifications(
    engine,
    espn_league_id: int,
    season: int,
    week: int,
    flags: Optional[List[dict]] = None,
) -> List:
    """
    Raise deduped handcuff_available notifications for high-priority
    flags only — a healthy starter's available handcuff stays a row in
    the panel, never a push. Idempotent (dedupe_key), safe to re-run.
    """
    if flags is None:
        flags = await available_handcuff_flags(engine, espn_league_id, season, week)
    created = []
    for flag in flags:
        if flag["priority"] != "high":
            continue
        title, body = _handcuff_notification_copy(flag, week)
        notification = await ensure_notification(
            engine,
            kind="handcuff_available",
            dedupe_key=(
                f"handcuff:{espn_league_id}:{season}:w{week}:{flag['starter_name']}"
            ),
            title=title,
            body=body,
            espn_league_id=espn_league_id,
            season=season,
            week=week,
        )
        if notification is not None:
            created.append(notification)
    return created
