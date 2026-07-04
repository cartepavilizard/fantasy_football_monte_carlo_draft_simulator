# -*- coding: utf-8 -*-
"""
Shared fixtures for the backend test suite.

The suite runs against an in-memory mongomock database. Two limitations
shape how the tests are written:
- mongomock has no session support, and odmantic wraps every save in one,
  so start_session is patched to hand odmantic a session of None.
- mongomock's aggregation cannot resolve odmantic Reference fields
  ($lookup with let), so endpoints that *fetch* Draft documents are
  tested by calling the route functions with a stubbed draft lookup
  (see test_draft_flow.py) rather than through the HTTP client.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DRAFT_YEAR", "2024")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import pytest
from mongomock_motor import AsyncMongoMockClient

DATA_DIR = BACKEND_DIR / "data"


class _NoopSession:
    async def __aenter__(self):
        return None  # odmantic then passes session=None to the collection ops

    async def __aexit__(self, *args):
        return False


async def _start_session(self):
    return _NoopSession()


AsyncMongoMockClient.start_session = _start_session


@pytest.fixture()
def app_module(monkeypatch):
    """The app module with its engine swapped for a fresh in-memory one"""
    from odmantic import AIOEngine
    import app as appmod

    engine = AIOEngine(
        client=AsyncMongoMockClient(), database="test-fantasy-football"
    )
    monkeypatch.setattr(appmod, "engine", engine)
    return appmod


@pytest.fixture()
def client(app_module):
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as test_client:
        yield test_client


def sample(name: str) -> bytes:
    return (DATA_DIR / name).read_bytes()


def upload(client, url: str, content: bytes, method: str = "post"):
    return getattr(client, method)(
        url, files={"file": ("upload.csv", content, "text/csv")}
    )


@pytest.fixture()
def league_id(client) -> str:
    """A league created from the sample teams file"""
    response = upload(client, "/league", sample("teams.csv"))
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.fixture()
def ready_league_id(client, league_id) -> str:
    """A league with players, historical players, and draft history loaded"""
    for url, filename in [
        (f"/league/{league_id}/player", "players.csv"),
        (f"/league/{league_id}/historical_player", "historical_players.csv"),
        (f"/league/{league_id}/historical_draft", "historical_drafts.csv"),
    ]:
        response = upload(client, url, sample(filename))
        assert response.status_code == 200, response.text
    return league_id
