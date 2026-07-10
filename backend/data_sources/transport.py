# -*- coding: utf-8 -*-
"""
PLUGGABLE HTTP TRANSPORT FOR SOURCE ADAPTERS

Adapters never talk to httpx directly — they go through a Transport so
that (a) tests can substitute a fake, and (b) sources that grow anti-bot
protection (FantasyPros is the flagged risk) can swap in a browser-backed
transport without touching adapter logic.
"""
from dataclasses import dataclass
import json
from typing import Optional, Protocol

DEFAULT_TIMEOUT_SECONDS = 30.0
# Honest, stable UA: these are low-frequency personal-use fetches
DEFAULT_USER_AGENT = "ff-monte-carlo-draft-simulator/0.1 (personal use)"


@dataclass
class TransportResponse:
    """Transport-agnostic response envelope (also what the cache stores)"""

    status_code: int
    text: str
    url: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        return json.loads(self.text)


class Transport(Protocol):
    async def get(
        self, url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None
    ) -> TransportResponse: ...

    async def post(
        self, url: str, *, data: Optional[dict] = None, headers: Optional[dict] = None
    ) -> TransportResponse: ...

    async def aclose(self) -> None: ...


class HttpxTransport:
    """Default transport: plain HTTP via a lazily created httpx client"""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self._timeout = timeout
        self._user_agent = user_agent
        self._client = None

    def _client_instance(self):
        if self._client is None:
            import httpx  # deferred so importing the package never requires it

            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
                follow_redirects=True,
            )
        return self._client

    async def get(self, url, *, params=None, headers=None) -> TransportResponse:
        response = await self._client_instance().get(
            url, params=params, headers=headers
        )
        return TransportResponse(
            status_code=response.status_code,
            text=response.text,
            url=str(response.url),
        )

    async def post(self, url, *, data=None, headers=None) -> TransportResponse:
        response = await self._client_instance().post(
            url, data=data, headers=headers
        )
        return TransportResponse(
            status_code=response.status_code,
            text=response.text,
            url=str(response.url),
        )

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class PlaywrightTransport:
    """
    Reserved seam for anti-bot sources (Phase 2, FantasyPros fallback).
    Deliberately unimplemented in Phase 0 so no adapter accidentally
    depends on a browser being available.
    """

    async def get(self, url, *, params=None, headers=None) -> TransportResponse:
        raise NotImplementedError(
            "PlaywrightTransport is a Phase 2 fallback; use HttpxTransport"
        )

    async def aclose(self):
        return None
