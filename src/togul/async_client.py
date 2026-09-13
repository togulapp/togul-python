from __future__ import annotations

import asyncio
from types import TracebackType
from typing import List, Mapping, Optional, Type

import httpx

from . import _transport as t
from .cache import TTLCache
from .client import CacheListener, safe_json
from .config import Config
from .errors import TogulError
from .result import EvaluateResult


class AsyncTogulClient:
    """Asyncio Togul client for FastAPI, Starlette and other ASGI apps.

    ``invalidate_cache`` and ``invalidate_flag`` stay synchronous: they only
    touch the in-memory cache under a lock, so awaiting them would buy
    nothing.
    """

    def __init__(
        self,
        config: Config,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._config = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout,
        )
        self._cache = TTLCache(config.cache_ttl)
        self._listeners: List[CacheListener] = []

    @property
    def config(self) -> Config:
        return self._config

    @property
    def cache(self) -> TTLCache:
        return self._cache

    async def evaluate(
        self,
        flag_key: str,
        context: Optional[Mapping[str, str]] = None,
    ) -> EvaluateResult:
        key = t.cache_key_for(flag_key, self._config.environment, context)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = await self._fetch(flag_key, context)
        self._cache.set(key, result)
        return result

    def invalidate_cache(self) -> None:
        self._cache.flush()
        self._notify("")

    def invalidate_flag(self, flag_key: str) -> None:
        self._cache.invalidate_flag(flag_key)
        self._notify(flag_key)

    def on_cache_invalidated(self, listener: CacheListener) -> None:
        self._listeners.append(listener)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncTogulClient:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.aclose()

    async def _fetch(
        self,
        flag_key: str,
        context: Optional[Mapping[str, str]],
    ) -> EvaluateResult:
        t.require_api_key(self._config)
        payload = t.build_request(self._config, flag_key, context)
        headers = {"X-API-Key": self._config.api_key}
        last_error: Optional[BaseException] = None

        for attempt in range(self._config.retry_count):
            if attempt > 0:
                await asyncio.sleep(t.backoff_seconds(attempt))

            try:
                response = await self._http.post(t.EVALUATE_PATH, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            if response.status_code >= 400:
                error = t.api_error_from(response.status_code, safe_json(response))
                if not t.should_retry(response.status_code):
                    raise error
                last_error = error
                continue

            return t.parse_result(safe_json(response), flag_key)

        raise TogulError(f"togul: all retries failed: {last_error}") from last_error

    def _notify(self, flag_key: str) -> None:
        for listener in self._listeners:
            listener(flag_key)
