# -*- coding: utf-8 -*-
"""
Data-source foundations: transport seam, rate limiting, raw-response
caching, batch packaging with name resolution, and the Phase 0 Mongo
collection models round-tripping through the (mock) engine.
"""
import asyncio
import time

import pytest
from conftest import FakeTransport
from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from data_sources.base import BaseSourceAdapter, SourceFetchError, SourceRecord
from data_sources.cache import RawResponseCache
from data_sources.ratelimit import RateLimiter
from data_sources.resolver import PlayerResolver
from data_sources.transport import TransportResponse
from models.sources import (
    BlendedRanking,
    BlendedRankingRecord,
    HistoricalPick,
    OwnerProfile,
    PlayerAlias,
    SourceRankingBatch,
)


class DummyAdapter(BaseSourceAdapter):
    source_name = "dummy"
    min_request_interval_seconds = 0.0

    async def fetch(self, season, scoring_format):
        response = await self._get("https://example.test/rankings")
        return [SourceRecord(**row) for row in response.json()]


PAYLOAD = [
    {"raw_name": "AJ Brown", "position": "WR", "nfl_team": "PHI", "adp": 8.5},
    {"raw_name": "Nobody Realmann", "position": "RB", "rank": 999},
]

POOL = [
    {"name": "A.J. Brown", "position": "WR", "nfl_team": "PHI"},
    {"name": "Bijan Robinson", "position": "RB", "nfl_team": "ATL"},
]


# --- caching ---------------------------------------------------------------


def test_cache_avoids_second_transport_hit(tmp_path):
    transport = FakeTransport(payload=PAYLOAD)
    cache = RawResponseCache(tmp_path, ttl_seconds=3600)
    adapter = DummyAdapter(transport=transport, cache=cache)
    first = asyncio.run(adapter.fetch(2026, "ppr"))
    second = asyncio.run(adapter.fetch(2026, "ppr"))
    assert transport.calls == 1
    assert [r.raw_name for r in first] == [r.raw_name for r in second]


def test_cache_expires_after_ttl(tmp_path, monkeypatch):
    transport = FakeTransport(payload=PAYLOAD)
    cache = RawResponseCache(tmp_path, ttl_seconds=100)
    adapter = DummyAdapter(transport=transport, cache=cache)
    asyncio.run(adapter.fetch(2026, "ppr"))
    monkeypatch.setattr(time, "time", lambda now=time.time(): now + 101)
    asyncio.run(adapter.fetch(2026, "ppr"))
    assert transport.calls == 2


def test_corrupt_cache_entry_is_treated_as_miss(tmp_path):
    cache = RawResponseCache(tmp_path, ttl_seconds=3600)
    response = TransportResponse(status_code=200, text="{}", url="u")
    cache.put("dummy", "u", None, response)
    for path in (tmp_path / "dummy").iterdir():
        path.write_text("not json")
    assert cache.get("dummy", "u", None) is None


# --- rate limiting ---------------------------------------------------------


def test_rate_limiter_spaces_consecutive_calls():
    limiter = RateLimiter(min_interval_seconds=0.05)

    async def three_passes():
        start = time.monotonic()
        for _ in range(3):
            await limiter.wait()
        return time.monotonic() - start

    assert asyncio.run(three_passes()) >= 0.1  # two enforced gaps


# --- error handling --------------------------------------------------------


def test_http_error_raises_source_fetch_error():
    adapter = DummyAdapter(transport=FakeTransport(status_code=503))
    with pytest.raises(SourceFetchError, match="dummy.*503"):
        asyncio.run(adapter.fetch(2026, "ppr"))


# --- batch packaging -------------------------------------------------------


def test_fetch_batch_resolves_names_and_flags_unresolved():
    adapter = DummyAdapter(transport=FakeTransport(payload=PAYLOAD))
    resolver = PlayerResolver(POOL)
    batch = asyncio.run(adapter.fetch_batch(2026, "ppr", resolver=resolver))
    assert batch.success is True
    assert batch.source == "dummy"
    by_raw = {record.raw_name: record for record in batch.records}
    assert by_raw["AJ Brown"].canonical_name == "A.J. Brown"
    assert by_raw["AJ Brown"].resolution_method == "exact"
    assert by_raw["AJ Brown"].adp == 8.5
    assert by_raw["Nobody Realmann"].canonical_name is None
    assert by_raw["Nobody Realmann"].resolution_method == "unresolved"


def test_fetch_batch_packages_failure_instead_of_raising():
    adapter = DummyAdapter(transport=FakeTransport(status_code=500))
    batch = asyncio.run(adapter.fetch_batch(2026, "ppr"))
    assert batch.success is False
    assert "500" in batch.error
    assert batch.records == []


# --- collection models round-trip ------------------------------------------


def test_phase0_models_round_trip_through_engine():
    engine = AIOEngine(client=AsyncMongoMockClient(), database="test-sources")

    async def round_trip():
        adapter = DummyAdapter(transport=FakeTransport(payload=PAYLOAD))
        batch = await adapter.fetch_batch(2026, "ppr", resolver=PlayerResolver(POOL))
        await engine.save(batch)
        await engine.save(
            BlendedRanking(
                season=2026,
                scoring_format="ppr",
                source_weights={"dummy": 1.0},
                sources_used=["dummy"],
                records=[
                    BlendedRankingRecord(
                        canonical_name="A.J. Brown",
                        position="wr",
                        blended_value=1.7,
                        adp=8.5,
                        source_values={"dummy": 1.7},
                    )
                ],
            )
        )
        await engine.save(
            HistoricalPick(
                espn_league_id=61119864,
                season=2019,
                overall_pick=1,
                round_num=1,
                round_pick=1,
                member_guid="{ABC-123}",
                espn_team_id=4,
                raw_player_name="Saquon Barkley",
                position="rb",
            )
        )
        await engine.save(
            OwnerProfile(profile_key="dave", member_guids=["{ABC-123}"])
        )
        await engine.save(
            PlayerAlias(alias="scary terry", canonical_name="Terry McLaurin")
        )

        saved_batch = await engine.find_one(
            SourceRankingBatch, SourceRankingBatch.source == "dummy"
        )
        assert saved_batch.records[0].canonical_name == "A.J. Brown"
        pick = await engine.find_one(HistoricalPick, HistoricalPick.season == 2019)
        assert pick.member_guid == "{ABC-123}"
        assert pick.is_keeper is False
        profile = await engine.find_one(
            OwnerProfile, OwnerProfile.profile_key == "dave"
        )
        assert profile.metrics == {}
        blend = await engine.find_one(BlendedRanking, BlendedRanking.season == 2026)
        assert blend.records[0].blended_value == 1.7
        alias = await engine.find_one(PlayerAlias, PlayerAlias.alias == "scary terry")
        assert alias.canonical_name == "Terry McLaurin"

    asyncio.run(round_trip())


def test_alias_overrides_load_into_resolver():
    engine = AIOEngine(client=AsyncMongoMockClient(), database="test-aliases")

    async def load():
        from data_sources.resolver import load_alias_overrides

        await engine.save(
            PlayerAlias(alias="scary terry", canonical_name="Terry McLaurin")
        )
        return await load_alias_overrides(engine)

    overrides = asyncio.run(load())
    resolver = PlayerResolver(POOL, overrides=overrides)
    assert resolver.resolve("Scary Terry").canonical_name == "Terry McLaurin"
