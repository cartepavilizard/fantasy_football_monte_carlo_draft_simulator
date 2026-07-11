# -*- coding: utf-8 -*-
"""
C7 (frontier core): the curated handcuff map. Seeding is additive-only
(manual edits and deletions always survive a re-seed), the CRUD marks
edits as manual, and the whole surface is Mongo-only. The flagging
logic that joins this map against rosters/free agents is the cheap
half (spec in models/handcuffs.py).
"""
import asyncio

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.handcuffs import (
    SEED_HANDCUFF_PAIRS,
    HandcuffPair,
    delete_handcuff,
    list_handcuffs,
    seed_handcuffs,
    upsert_handcuff,
)


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-handcuffs")


def test_seed_inserts_all_pairs_once():
    engine = make_engine()
    first = asyncio.run(seed_handcuffs(engine))
    assert first == {"created": len(SEED_HANDCUFF_PAIRS), "skipped": 0}
    again = asyncio.run(seed_handcuffs(engine))
    assert again == {"created": 0, "skipped": len(SEED_HANDCUFF_PAIRS)}
    pairs = asyncio.run(list_handcuffs(engine))
    by_starter = {pair.starter_name: pair.handcuff_name for pair in pairs}
    # the one mapping this user will definitely check first
    assert by_starter["Kenneth Walker III"] == "Zach Charbonnet"


def test_reseeding_never_clobbers_manual_edits_or_deletions():
    engine = make_engine()

    async def go():
        await seed_handcuffs(engine)
        # camp news: repoint one, delete a backfield gone committee
        await upsert_handcuff(engine, "James Cook", "New Rookie")
        await delete_handcuff(engine, "Isiah Pacheco")
        await seed_handcuffs(engine)
        return await list_handcuffs(engine)

    pairs = asyncio.run(go())
    by_starter = {pair.starter_name: pair for pair in pairs}
    assert by_starter["James Cook"].handcuff_name == "New Rookie"
    assert by_starter["James Cook"].source == "manual"
    # the deletion is a curation decision (committee backfield) — the
    # re-seed must NOT resurrect it (soft delete)
    assert "Isiah Pacheco" not in by_starter


def test_upsert_creates_and_repoints():
    engine = make_engine()

    async def go():
        created = await upsert_handcuff(
            engine, "New Starter", "Backup A", nfl_team="SEA", note="camp"
        )
        repointed = await upsert_handcuff(engine, "New Starter", "Backup B")
        rows = await engine.find(
            HandcuffPair, HandcuffPair.starter_name == "New Starter"
        )
        return created, repointed, rows

    created, repointed, rows = asyncio.run(go())
    assert created.source == "manual"
    assert repointed.handcuff_name == "Backup B"
    assert repointed.nfl_team == "SEA"  # untouched fields survive
    assert len(rows) == 1  # upsert, not duplicate


def test_delete_missing_returns_false_and_upsert_revives():
    engine = make_engine()

    async def go():
        missing = await delete_handcuff(engine, "Nobody")
        await upsert_handcuff(engine, "New Starter", "Backup A")
        await delete_handcuff(engine, "New Starter")
        twice = await delete_handcuff(engine, "New Starter")
        revived = await upsert_handcuff(engine, "New Starter", "Backup B")
        listed = await list_handcuffs(engine)
        return missing, twice, revived, listed

    missing, twice, revived, listed = asyncio.run(go())
    assert missing is False
    assert twice is False  # already soft-deleted
    assert revived.active is True
    assert [pair.starter_name for pair in listed] == ["New Starter"]


def test_handcuff_endpoints_crud(client):
    assert client.get("/inseason/handcuffs").json() == {"handcuffs": []}

    seeded = client.post("/inseason/handcuffs/seed").json()
    assert seeded["created"] == len(SEED_HANDCUFF_PAIRS)

    updated = client.post(
        "/inseason/handcuffs?starter_name=James+Cook&handcuff_name=New+Rookie"
    ).json()
    assert updated["source"] == "manual"

    listed = client.get("/inseason/handcuffs").json()["handcuffs"]
    by_starter = {pair["starter_name"]: pair for pair in listed}
    assert by_starter["James Cook"]["handcuff_name"] == "New Rookie"
    assert listed == sorted(listed, key=lambda pair: pair["starter_name"])

    assert client.delete("/inseason/handcuffs/James Cook").status_code == 200
    assert client.delete("/inseason/handcuffs/James Cook").status_code == 404
