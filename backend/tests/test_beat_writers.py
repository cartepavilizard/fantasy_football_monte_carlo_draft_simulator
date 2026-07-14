# -*- coding: utf-8 -*-
"""
D1: the curated team -> beat-writer directory. Seeding is additive-only
(manual edits and deletions always survive a re-seed), the CRUD marks
edits as manual, and the whole surface is Mongo-only — mirrors
test_handcuffs.py's coverage of the same pattern in handcuffs.py.
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.beat_writers import (
    SEED_BEAT_WRITERS,
    BeatWriter,
    delete_beat_writer,
    get_beat_writer,
    list_beat_writers,
    seed_beat_writers,
    upsert_beat_writer,
)


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-beat-writers")


def test_seed_inserts_all_32_teams_once():
    engine = make_engine()
    first = asyncio.run(seed_beat_writers(engine))
    assert first == {"created": len(SEED_BEAT_WRITERS), "skipped": 0}
    assert len(SEED_BEAT_WRITERS) == 32

    again = asyncio.run(seed_beat_writers(engine))
    assert again == {"created": 0, "skipped": len(SEED_BEAT_WRITERS)}

    writers = asyncio.run(list_beat_writers(engine))
    by_team = {writer.nfl_team: writer.writer_name for writer in writers}
    assert by_team["SEA"]  # this user's homer team, definitely seeded


def test_reseeding_never_clobbers_manual_edits_or_deletions():
    engine = make_engine()

    async def go():
        await seed_beat_writers(engine)
        await upsert_beat_writer(engine, "SEA", "New Beat Writer", "New Outlet")
        await delete_beat_writer(engine, "ATL")
        await seed_beat_writers(engine)
        return await list_beat_writers(engine)

    writers = asyncio.run(go())
    by_team = {writer.nfl_team: writer for writer in writers}
    assert by_team["SEA"].writer_name == "New Beat Writer"
    assert by_team["SEA"].source == "manual"
    # the deletion is a curation decision — re-seed must not resurrect it
    assert "ATL" not in by_team


def test_upsert_creates_and_repoints():
    engine = make_engine()

    async def go():
        created = await upsert_beat_writer(
            engine, "sea", "Writer A", "Outlet A", note="camp"
        )
        repointed = await upsert_beat_writer(engine, "SEA", "Writer B", "Outlet B")
        rows = await engine.find(BeatWriter, BeatWriter.nfl_team == "SEA")
        return created, repointed, rows

    created, repointed, rows = asyncio.run(go())
    assert created.nfl_team == "SEA"  # lowercase input normalized
    assert created.source == "manual"
    assert repointed.writer_name == "Writer B"
    assert repointed.outlet == "Outlet B"
    assert len(rows) == 1  # upsert, not duplicate


def test_delete_missing_returns_false_and_upsert_revives():
    engine = make_engine()

    async def go():
        missing = await delete_beat_writer(engine, "SEA")
        await upsert_beat_writer(engine, "SEA", "Writer A", "Outlet A")
        await delete_beat_writer(engine, "SEA")
        twice = await delete_beat_writer(engine, "SEA")
        revived = await upsert_beat_writer(engine, "SEA", "Writer B", "Outlet B")
        listed = await list_beat_writers(engine)
        return missing, twice, revived, listed

    missing, twice, revived, listed = asyncio.run(go())
    assert missing is False
    assert twice is False  # already soft-deleted
    assert revived.active is True
    assert [writer.nfl_team for writer in listed] == ["SEA"]


def test_get_beat_writer_returns_none_for_unknown_or_inactive_team():
    engine = make_engine()

    async def go():
        none_at_all = await get_beat_writer(engine, "SEA")
        await upsert_beat_writer(engine, "SEA", "Writer A", "Outlet A")
        found = await get_beat_writer(engine, "sea")  # case-insensitive
        await delete_beat_writer(engine, "SEA")
        after_delete = await get_beat_writer(engine, "SEA")
        empty_team = await get_beat_writer(engine, "")
        return none_at_all, found, after_delete, empty_team

    none_at_all, found, after_delete, empty_team = asyncio.run(go())
    assert none_at_all is None
    assert found is not None and found.writer_name == "Writer A"
    assert after_delete is None
    assert empty_team is None


def test_writer_endpoints_crud(client):
    assert client.get("/inseason/writers").json() == {"writers": []}

    seeded = client.post("/inseason/writers/seed").json()
    assert seeded["created"] == len(SEED_BEAT_WRITERS)

    updated = client.post(
        "/inseason/writers?nfl_team=SEA&writer_name=New+Writer&outlet=New+Outlet"
    ).json()
    assert updated["source"] == "manual"
    assert updated["writer_name"] == "New Writer"

    listed = client.get("/inseason/writers").json()["writers"]
    by_team = {writer["nfl_team"]: writer for writer in listed}
    assert by_team["SEA"]["writer_name"] == "New Writer"
    assert listed == sorted(listed, key=lambda writer: writer["nfl_team"])

    assert client.delete("/inseason/writers/SEA").status_code == 200
    assert client.delete("/inseason/writers/SEA").status_code == 404
