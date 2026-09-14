from __future__ import annotations

import httpx
import pytest

from togul.cache import TTLCache
from togul.errors import TogulAPIError
from togul.result import EvaluateResult
from togul.stream import INITIAL_BACKOFF, MAX_BACKOFF, AsyncTogulStreamClient, TogulStreamClient


def result(flag_key: str) -> EvaluateResult:
    return EvaluateResult(flag_key, True, "boolean", True, "rule_match")


def filled_cache() -> TTLCache:
    cache = TTLCache(30.0)
    cache.set("theme:production", result("theme"))
    cache.set("theme:production:user_id=1", result("theme"))
    cache.set("other:production", result("other"))
    return cache


def sse(body: str, status: int = 200) -> httpx.Response:
    if status != 200:
        return httpx.Response(status, json={"code": "denied", "message": "no"})
    return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})


def build(config, cache, responses, sleeps=None):
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == config.api_key
        assert request.headers["Accept"] == "text/event-stream"
        assert request.url.path == "/api/v1/stream"
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=config.base_url)
    client = TogulStreamClient(config, cache, http_client=http)
    if sleeps is not None:
        # Stop after the recorded reconnect attempts so connect() terminates.
        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 4:
                client.stop()

        client._sleep = fake_sleep  # type: ignore[attr-defined]
    return client


def test_flag_event_invalidates_only_that_flag(config):
    cache = filled_cache()
    client = build(config, cache, [sse('data: {"type":"flag.updated","flag_key":"theme"}\n')])
    client.stop_after_first_stream = True
    client.connect()

    assert cache.get("theme:production") is None
    assert cache.get("theme:production:user_id=1") is None
    assert cache.get("other:production") is not None


def test_event_without_a_flag_key_flushes_everything(config):
    cache = filled_cache()
    client = build(config, cache, [sse('data: {"type":"rule.deleted"}\n')])
    client.stop_after_first_stream = True
    client.connect()

    assert cache.get("theme:production") is None
    assert cache.get("other:production") is None


def test_heartbeats_and_noise_are_ignored(config):
    cache = filled_cache()
    body = ": heartbeat\n\nevent: ping\ndata: not json\n"
    client = build(config, cache, [sse(body)])
    client.stop_after_first_stream = True
    client.connect()

    assert cache.get("theme:production") is not None
    assert cache.get("other:production") is not None


def test_listeners_receive_the_flag_key(config):
    cache = filled_cache()
    client = build(config, cache, [sse('data: {"flag_key":"theme"}\ndata: {}\n')])
    seen = []
    client.on_cache_invalidated(seen.append)
    client.stop_after_first_stream = True
    client.connect()

    assert seen == ["theme", ""]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_raised_without_reconnecting(config, status):
    cache = filled_cache()
    sleeps: list = []
    client = build(config, cache, [sse("", status)], sleeps)
    with pytest.raises(TogulAPIError) as exc:
        client.connect()
    assert exc.value.status_code == status
    assert sleeps == []


def test_server_errors_reconnect_with_exponential_backoff(config):
    cache = filled_cache()
    sleeps: list = []
    client = build(config, cache, [sse("", 500)], sleeps)
    client.connect()
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_backoff_is_capped(config):
    assert INITIAL_BACKOFF == 1.0
    assert MAX_BACKOFF == 30.0

    # Behavioural proof the cap is actually applied, not just declared: this
    # would still pass unchanged if `_next_backoff` used
    # `max(backoff * 2, MAX_BACKOFF)` instead of `min(...)` unless both the
    # below-cap doubling and the at/above-cap clamp are asserted.
    cache = filled_cache()
    client = build(config, cache, [sse("")])
    assert client._next_backoff(4.0) == 8.0
    assert client._next_backoff(16.0) == 30.0


def test_blank_api_key_raises(config):
    blank = type(config)(api_key="", environment="production", base_url=config.base_url)
    cache = filled_cache()
    sleeps: list = []
    client = build(blank, cache, [sse("")], sleeps)
    client.connect()
    # require_api_key raises a TogulError, which is retryable (not an auth
    # status), so the loop backs off rather than crashing the caller.
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_backoff_resets_to_initial_after_a_clean_disconnect(config):
    cache = filled_cache()
    sleeps: list = []
    # 500 -> the backoff climbs to 2.0; 200 -> a clean stream resets it to 1.0;
    # then 500s forever. The second sleep proves the reset: without it the
    # sequence would be [1.0, 2.0, 4.0, 8.0].
    client = build(
        config,
        cache,
        [sse("", 500), sse('data: {"flag_key":"theme"}\n'), sse("", 500)],
        sleeps,
    )
    client.connect()
    assert sleeps == [1.0, 1.0, 2.0, 4.0]


async def test_async_stream_invalidates_the_flag(config):
    cache = filled_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        return sse('data: {"flag_key":"theme"}\n')

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=config.base_url)
    client = AsyncTogulStreamClient(config, cache, http_client=http)
    client.stop_after_first_stream = True
    await client.connect()

    assert cache.get("theme:production") is None
    assert cache.get("other:production") is not None


@pytest.mark.parametrize("status", [401, 403])
async def test_async_auth_errors_are_raised_without_reconnecting(config, status):
    cache = filled_cache()
    sleeps: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        return sse("", status)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=config.base_url)
    client = AsyncTogulStreamClient(config, cache, http_client=http)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 4:
            client.stop()

    client._sleep = fake_sleep

    with pytest.raises(TogulAPIError) as exc:
        await client.connect()
    assert exc.value.status_code == status
    assert sleeps == []
