from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Callable, List, Mapping, Optional, Type

import httpx

from . import _transport as t
from .cache import TTLCache
from .config import Config
from .errors import TogulError
from .result import EvaluateResult

CacheListener = Callable[[str], None]


def safe_json(response: httpx.Response) -> Any:
    """Decode a response body, returning None when it is not JSON."""
    try:
        return response.json()
    except ValueError:
        return None


class TogulClient:
    """Synchronous Togul client.

    Use this from Django, Celery, scripts, and any other blocking context.
    For asyncio applications (FastAPI, Starlette) use ``AsyncTogulClient``.
    """

    def __init__(self, config: Config, http_client: Optional[httpx.Client] = None) -> None:
        self._config = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(
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
        """The cache this client reads. A stream client invalidates it."""
        return self._cache

    def evaluate(
        self,
        flag_key: str,
        context: Optional[Mapping[str, str]] = None,
    ) -> EvaluateResult:
        key = t.cache_key_for(flag_key, self._config.environment, context)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._fetch(flag_key, context)
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

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> TogulClient:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def _fetch(
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
                time.sleep(t.backoff_seconds(attempt))

            try:
                response = self._http.post(t.EVALUATE_PATH, json=payload, headers=headers)
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
