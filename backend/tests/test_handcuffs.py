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

from models.config import HOMER_TEAM
from models.handcuffs import (
    SEED_HANDCUFF_PAIRS,
    HandcuffPair,
    available_handcuff_flags,
    delete_handcuff,
    ensure_handcuff_notifications,
    list_handcuffs,
    seed_handcuffs,
    upsert_handcuff,
)
from models.inseason import FreeAgentEntry, FreeAgentSnapshot, RosterSlotEntry, TeamWeekRoster
from models.notifications import Notification

LEAGUE_ID = 111
SEASON = 2026
WEEK = 5


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-handcuffs")


async def _seed_flag_fixture(
    engine,
    starter_injury_status="questionable",
    handcuff_team="SEA",
    include_alt_free_agent=True,
):
    """One starter (RB, on team 1's roster) with a curated handcuff
    sitting in the free-agent pool; returns nothing, just seeds state"""
    await engine.save(
        TeamWeekRoster(
            espn_league_id=LEAGUE_ID,
            season=SEASON,
            week=WEEK,
            espn_team_id=1,
            entries=[
                RosterSlotEntry(
                    player_id=1,
                    player_name="Kenneth Walker III",
                    position="RB",
                    nfl_team=handcuff_team,
                    lineup_slot="RB",
                    injury_status=starter_injury_status,
                    projected_points=12.0,
                )
            ],
        )
    )
    entries = [
        FreeAgentEntry(
            player_id=2,
            player_name="Zach Charbonnet",
            position="RB",
            nfl_team=handcuff_team,
            percent_owned=22.5,
            projected_points=9.0,
        )
    ]
    if include_alt_free_agent:
        entries.append(
            FreeAgentEntry(
                player_id=3,
                player_name="Some Other RB",
                position="RB",
                nfl_team="ATL",
                percent_owned=5.0,
                projected_points=4.0,
            )
        )
    await engine.save(
        FreeAgentSnapshot(
            espn_league_id=LEAGUE_ID, season=SEASON, week=WEEK, entries=entries
        )
    )
    await upsert_handcuff(
        engine, "Kenneth Walker III", "Zach Charbonnet", nfl_team=handcuff_team
    )


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


# --- flagging (C7's cheap half): joining the map against synced data --------


def test_flags_empty_when_nothing_synced_or_no_pairs_curated():
    engine = make_engine()
    assert asyncio.run(available_handcuff_flags(engine, LEAGUE_ID, SEASON, WEEK)) == []

    asyncio.run(seed_handcuffs(engine))  # pairs exist, but nothing rostered
    assert asyncio.run(available_handcuff_flags(engine, LEAGUE_ID, SEASON, WEEK)) == []


def test_flag_emitted_only_when_starter_rostered_and_handcuff_a_free_agent():
    engine = make_engine()

    async def go():
        await _seed_flag_fixture(engine, starter_injury_status=None)
        return await available_handcuff_flags(engine, LEAGUE_ID, SEASON, WEEK)

    (flag,) = asyncio.run(go())
    assert flag["starter_name"] == "Kenneth Walker III"
    assert flag["handcuff_name"] == "Zach Charbonnet"
    assert flag["nfl_team"] == "SEA"
    assert flag["starter_team_id"] == 1
    assert flag["handcuff_projected_points"] == 9.0
    assert flag["handcuff_percent_owned"] == 22.5
    # a healthy starter's spare-parts handcuff is normal, not urgent
    assert flag["priority"] == "normal"


def test_priority_high_only_for_questionable_doubtful_or_out():
    for status, expected in [
        ("questionable", "high"),
        ("doubtful", "high"),
        ("out", "high"),
        ("healthy", "normal"),
        (None, "normal"),
    ]:
        engine = make_engine()

        async def go(status=status):
            await _seed_flag_fixture(
                engine, starter_injury_status=status, handcuff_team="ATL"
            )
            return await available_handcuff_flags(engine, LEAGUE_ID, SEASON, WEEK)

        (flag,) = asyncio.run(go())
        assert flag["priority"] == expected, status


def test_no_flag_when_handcuff_not_in_free_agent_pool():
    engine = make_engine()

    async def go():
        await engine.save(
            TeamWeekRoster(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                week=WEEK,
                espn_team_id=1,
                entries=[
                    RosterSlotEntry(
                        player_id=1,
                        player_name="Kenneth Walker III",
                        position="RB",
                        nfl_team="SEA",
                        lineup_slot="RB",
                        injury_status="out",
                    )
                ],
            )
        )
        await upsert_handcuff(engine, "Kenneth Walker III", "Zach Charbonnet")
        # no FreeAgentSnapshot at all — the handcuff isn't sitting anywhere
        return await available_handcuff_flags(engine, LEAGUE_ID, SEASON, WEEK)

    assert asyncio.run(go()) == []


def test_homer_check_attached_only_for_homer_team_handcuffs():
    engine = make_engine()

    async def go_homer():
        await _seed_flag_fixture(engine, handcuff_team=HOMER_TEAM)
        return await available_handcuff_flags(engine, LEAGUE_ID, SEASON, WEEK)

    (flag,) = asyncio.run(go_homer())
    assert flag["homer_check"] is not None
    assert flag["homer_check"]["suggested"]["name"] == "Zach Charbonnet"
    assert flag["homer_check"]["homer_team"] == HOMER_TEAM
    assert flag["homer_check"]["alternatives"][0]["name"] == "Some Other RB"

    engine2 = make_engine()

    async def go_non_homer():
        await _seed_flag_fixture(engine2, handcuff_team="ATL")
        return await available_handcuff_flags(engine2, LEAGUE_ID, SEASON, WEEK)

    (flag2,) = asyncio.run(go_non_homer())
    assert flag2["homer_check"] is None


# --- notifications: high priority only, insurance/opportunity framing -------


def test_notification_fires_only_for_high_priority():
    engine = make_engine()

    async def go():
        await _seed_flag_fixture(
            engine, starter_injury_status="healthy", handcuff_team="ATL"
        )
        created = await ensure_handcuff_notifications(engine, LEAGUE_ID, SEASON, WEEK)
        return created, await engine.find(Notification)

    created, stored = asyncio.run(go())
    assert created == []
    assert stored == []


def test_notification_dedupes_and_never_mentions_points():
    engine = make_engine()

    async def go():
        await _seed_flag_fixture(
            engine, starter_injury_status="out", handcuff_team="ATL"
        )
        first = await ensure_handcuff_notifications(engine, LEAGUE_ID, SEASON, WEEK)
        again = await ensure_handcuff_notifications(engine, LEAGUE_ID, SEASON, WEEK)
        return first, again

    first, again = asyncio.run(go())
    assert len(first) == 1
    assert again == []  # deduped on the second pass

    notification = first[0]
    assert notification.kind == "handcuff_available"
    assert (
        notification.dedupe_key
        == f"handcuff:{LEAGUE_ID}:{SEASON}:w{WEEK}:Kenneth Walker III"
    )
    assert "Zach Charbonnet" in notification.body
    assert "workload" in notification.body
    assert "pts" not in notification.body
    assert "points" not in notification.body.lower()
