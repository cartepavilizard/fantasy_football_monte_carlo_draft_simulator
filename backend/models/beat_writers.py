# -*- coding: utf-8 -*-
"""
BEAT-WRITER DIRECTORY (PHASE D, TASK D1 — sourced once, here)

A curated team -> beat-writer table, following handcuffs.py's pattern
exactly: a CURATED SEED TABLE in Mongo with CRUD, not a scrape. The
mapping (who covers a team closely enough to be worth asking Grok
about) is fantasy-community/media-landscape knowledge that shifts a
few times a season (a writer changes beats or outlets) — a curated
row with manual-edit override beats a scraper against sites with no
stable "beat writer" field to begin with.

The seed below reflects this codebase's training-data knowledge of
each team's primary beat writer and MUST get a human review pass
before relying on it — beat assignments and outlet affiliations churn
outside this codebase's visibility, exactly like handcuffs.py's
end-of-season depth charts. seed_beat_writers() only inserts missing
teams, so manual edits and deletions always survive re-seeding.

Consumer: D3's grok_prompt template `beat_check` looks up the
requested player's nfl_team here to name a specific writer/outlet in
the generated prompt; an unknown team degrades to team-level phrasing
rather than failing.

Endpoints: GET/POST/DELETE under /inseason/writers (inseason_api.py),
Mongo-only like every route in that router.
"""
import datetime
from typing import List, Optional

from odmantic import Model
from odmantic import Field as ODField
from odmantic import query


class BeatWriter(Model):
    """One NFL team's primary beat writer (nfl_team is the key)"""

    model_config = {"collection": "beat_writers"}

    nfl_team: str  # e.g. "SEA"
    writer_name: str
    outlet: str
    note: Optional[str] = None
    source: str = "seed"  # seed | manual
    # deletions are soft so they survive re-seeding, same reasoning as
    # HandcuffPair.active: "no writer worth naming" is a curation
    # decision too, and must not resurrect on the next seed pass
    active: bool = True
    updated_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# Best-knowledge seed — REVIEW BEFORE RELYING ON IT (see module
# docstring). (nfl_team, writer_name, outlet, optional note)
SEED_BEAT_WRITERS = [
    ("ARI", "Bob McManaman", "Arizona Republic", None),
    ("ATL", "D. Orlando Ledbetter", "Atlanta Journal-Constitution", None),
    ("BAL", "Jeff Zrebiec", "The Athletic", None),
    ("BUF", "Jay Skurski", "Buffalo News", None),
    ("CAR", "Joseph Person", "The Athletic", None),
    ("CHI", "Brad Biggs", "Chicago Tribune", None),
    ("CIN", "Paul Dehner Jr.", "The Athletic", None),
    ("CLE", "Mary Kay Cabot", "Cleveland.com", None),
    ("DAL", "Jon Machota", "The Athletic", None),
    ("DEN", "Mike Klis", "9News", None),
    ("DET", "Dave Birkett", "Detroit Free Press", None),
    ("GB", "Rob Demovsky", "ESPN", None),
    ("HOU", "Aaron Wilson", "Houston Chronicle", "verify current beat"),
    ("IND", "Joel A. Erickson", "Indianapolis Star", None),
    ("JAX", "John Reid", "The Athletic", "verify current beat"),
    ("KC", "Nate Taylor", "The Athletic", None),
    ("LAC", "Daniel Popper", "The Athletic", None),
    ("LAR", "Jourdan Rodrigue", "The Athletic", None),
    ("LV", "Vic Tafur", "The Athletic", "verify current beat"),
    ("MIA", "Barry Jackson", "Miami Herald", None),
    ("MIN", "Chad Graff", "The Athletic", None),
    ("NE", "Mike Reiss", "ESPN", None),
    ("NO", "Katherine Terrell", "The Athletic", None),
    ("NYG", "Dan Duggan", "The Athletic", None),
    ("NYJ", "Zack Rosenblatt", "The Athletic", None),
    ("PHI", "Zach Berman", "The Athletic", None),
    ("PIT", "Brooke Pryor", "ESPN", "verify current beat"),
    ("SEA", "Bob Condotta", "Seattle Times", None),
    ("SF", "Matt Barrows", "The Athletic", None),
    ("TB", "Greg Auman", "The Athletic", None),
    ("TEN", "Joe Rexrode", "The Athletic", None),
    ("WAS", "Ben Standig", "The Athletic", None),
]


async def seed_beat_writers(engine, rows=None) -> dict:
    """
    Insert any seed row whose team is not already known. Existing rows
    (manual edits, repointed writers, soft-deleted teams alike) are
    never touched, so re-seeding is always safe.
    """
    if rows is None:
        rows = SEED_BEAT_WRITERS
    existing = {
        writer.nfl_team for writer in await engine.find(BeatWriter)
    }  # includes inactive rows: a deletion is a curation decision too
    created = 0
    for nfl_team, writer_name, outlet, note in rows:
        if nfl_team in existing:
            continue
        await engine.save(
            BeatWriter(
                nfl_team=nfl_team,
                writer_name=writer_name,
                outlet=outlet,
                note=note,
                source="seed",
            )
        )
        created += 1
    return {"created": created, "skipped": len(rows) - created}


async def list_beat_writers(engine) -> List[BeatWriter]:
    return await engine.find(
        BeatWriter,
        BeatWriter.active == True,  # noqa: E712
        sort=query.asc(BeatWriter.nfl_team),
    )


async def get_beat_writer(engine, nfl_team: str) -> Optional[BeatWriter]:
    """Active writer for one team, or None (D3's prompt builder join)"""
    if not nfl_team:
        return None
    return await engine.find_one(
        BeatWriter,
        (BeatWriter.nfl_team == nfl_team.upper()) & (BeatWriter.active == True),  # noqa: E712
    )


async def upsert_beat_writer(
    engine,
    nfl_team: str,
    writer_name: str,
    outlet: str,
    note: Optional[str] = None,
) -> BeatWriter:
    """Create or repoint one team's writer; manual edits win over seeds"""
    nfl_team = nfl_team.upper()
    writer = await engine.find_one(BeatWriter, BeatWriter.nfl_team == nfl_team)
    if writer is None:
        writer = BeatWriter(nfl_team=nfl_team, writer_name=writer_name, outlet=outlet)
    writer.writer_name = writer_name
    writer.outlet = outlet
    if note is not None:
        writer.note = note
    writer.source = "manual"
    writer.active = True  # editing a soft-deleted row revives it
    writer.updated_at = datetime.datetime.now()
    await engine.save(writer)
    return writer


async def delete_beat_writer(engine, nfl_team: str) -> bool:
    """Soft delete: the row stays so re-seeding cannot resurrect it"""
    writer = await engine.find_one(
        BeatWriter, BeatWriter.nfl_team == nfl_team.upper()
    )
    if writer is None or not writer.active:
        return False
    writer.active = False
    writer.source = "manual"
    writer.updated_at = datetime.datetime.now()
    await engine.save(writer)
    return True
