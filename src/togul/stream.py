from __future__ import annotations

import asyncio
import time
from typing import List, Optional

import httpx

from . import _transport as t
from ._sse import parse_sse_event
from .cache import TTLCache
from .client import CacheListener, safe_json
from .config import Config
from .errors import TogulAPIError

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 30.0


def _is_auth_error(exc: BaseException) -> bool:
    return isinstance(exc, TogulAPIError) and exc.status_code in (401, 403)


def _stream_timeout(config: Config) -> httpx.Timeout:
    """No read deadline: an SSE connection is intentionally long-lived."""
    return httpx.Timeout(None, connect=config.timeout)


class _BaseStreamClient:
    def __init__(self, config: Config, cache: TTLCache) -> None:
        self._config = config
        self._cache = cache
        self._listeners: List[CacheListener] = []
        self._stop = False
        #: Internal test hook: end connect() after one stream attempt.
        self.stop_after_first_stream = False

    def on_cache_invalidated(self, listener: CacheListener) -> None:
        self._listeners.append(listener)

    def stop(self) -> None:
        """Ask the reconnect loop to exit at the next opportunity."""
        self._stop = True

    def _handle_line(self, line: str) -> None:
        event = parse_sse_event(line)
        if event is None:
            return

        flag_key = str(event.get("flag_key") or "")
        if flag_key:
            self._cache.invalidate_flag(flag_key)
        else:
            self._cache.flush()

        for listener in self._listeners:
            listener(flag_key)

    def _next_backoff(self, backoff: float) -> float:
        return min(backoff * 2, MAX_BACKOFF)


class TogulStreamClient(_BaseStreamClient):
    """Blocking SSE client that keeps a cache fresh.

    ``connect()`` never returns on its own: it reconnects with exponential
    backoff until ``stop()`` is called, or until the API rejects the key
    with 401/403 — which is re-raised, because retrying cannot fix it.
    """

    def __init__(
        self,
        config: Config,
        cache: TTLCache,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(config, cache)
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(
            base_url=config.base_url,
            timeout=_stream_timeout(config),
        )

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def connect(self) -> None:
        backoff = INITIAL_BACKOFF
        while not self._stop:
            try:
                self._stream_once()
                backoff = INITIAL_BACKOFF
            except Exception as exc:
                if _is_auth_error(exc):
                    raise
                if self._stop:
                    return
                self._sleep(backoff)
                backoff = self._next_backoff(backoff)
            if self.stop_after_first_stream:
                return

    def close(self) -> None:
        self.stop()
        if self._owns_http:
            self._http.close()

    def _stream_once(self) -> None:
        t.require_api_key(self._config)
        headers = {
            "Accept": "text/event-stream",
            "X-API-Key": self._config.api_key,
        }
        with self._http.stream("GET", t.STREAM_PATH, headers=headers) as response:
            if response.status_code != 200:
                response.read()
                raise t.api_error_from(response.status_code, safe_json(response))
            for line in response.iter_lines():
                if self._stop:
                    return
                self._handle_line(line)


class AsyncTogulStreamClient(_BaseStreamClient):
    """Asyncio SSE client. Run it as a background task."""

    def __init__(
        self,
        config: Config,
        cache: TTLCache,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(config, cache)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=_stream_timeout(config),
        )

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def connect(self) -> None:
        backoff = INITIAL_BACKOFF
        while not self._stop:
            try:
                await self._stream_once()
                backoff = INITIAL_BACKOFF
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if _is_auth_error(exc):
                    raise
                if self._stop:
                    return
                await self._sleep(backoff)
                backoff = self._next_backoff(backoff)
            if self.stop_after_first_stream:
                return

    async def aclose(self) -> None:
        self.stop()
        if self._owns_http:
            await self._http.aclose()

    async def _stream_once(self) -> None:
        t.require_api_key(self._config)
        headers = {
            "Accept": "text/event-stream",
            "X-API-Key": self._config.api_key,
        }
        async with self._http.stream("GET", t.STREAM_PATH, headers=headers) as response:
            if response.status_code != 200:
                await response.aread()
                raise t.api_error_from(response.status_code, safe_json(response))
            async for line in response.aiter_lines():
                if self._stop:
                    return
                self._handle_line(line)
