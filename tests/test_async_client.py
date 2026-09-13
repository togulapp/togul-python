from __future__ import annotations

import httpx
import pytest

from togul.async_client import AsyncTogulClient
from togul.errors import TogulAPIError, TogulError

from .conftest import Recorder, err, make_transport, ok


class _AsyncByteStreamWrapper(httpx.AsyncByteStream):
    """Wraps content in an async byte stream for testing."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.consumed = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        if self.consumed:
            raise httpx.StreamConsumed()
        self.consumed = True
        yield self.content

    async def aclose(self) -> None:  # type: ignore[no-untyped-def]
        pass


class AsyncMockTransport(httpx.AsyncBaseTransport):
    """Async-compatible mock transport for testing."""

    def __init__(self, responses, recorder=None):
        self.responses = list(responses)
        self.recorder = recorder

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.recorder is not None:
            self.recorder.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        # Convert the response stream to async for AsyncClient compatibility
        if not isinstance(response.stream, httpx.AsyncByteStream):
            # Create a new response with an async byte stream
            content = response.content
            async_stream = _AsyncByteStreamWrapper(content)
            response = httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=async_stream,
                extensions=response.extensions,
            )
        return response


def build(config, responses, recorder=None) -> AsyncTogulClient:
    transport = AsyncMockTransport(responses, recorder)
    http = httpx.AsyncClient(transport=transport, base_url=config.base_url)
    return AsyncTogulClient(config, http_client=http)


@pytest.fixture
def no_async_sleep(monkeypatch):
    import togul.async_client

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(togul.async_client.asyncio, "sleep", fake_sleep)
    return slept


async def test_evaluate_returns_the_parsed_result(config):
    client = build(config, [ok()])
    result = await client.evaluate("new-dashboard")
    assert result.enabled is True
    assert result.value is True


async def test_second_evaluate_is_served_from_cache(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    await client.evaluate("f")
    await client.evaluate("f")
    assert recorder.count == 1


async def test_429_is_retried_then_succeeds(config, no_async_sleep):
    recorder = Recorder()
    client = build(config, [err(429, "rate_limited", "slow down"), ok()], recorder)
    assert (await client.evaluate("f")).enabled is True
    assert recorder.count == 2
    assert no_async_sleep == [pytest.approx(0.1)]


async def test_400_raises_immediately(config, no_async_sleep):
    recorder = Recorder()
    client = build(config, [err(400, "invalid", "bad flag")], recorder)
    with pytest.raises(TogulAPIError) as exc:
        await client.evaluate("f")
    assert exc.value.status_code == 400
    assert recorder.count == 1


async def test_retry_exhaustion_raises(config, no_async_sleep):
    recorder = Recorder()
    client = build(config, [err(500)], recorder)
    with pytest.raises(TogulError):
        await client.evaluate("f")
    assert recorder.count == 2


async def test_blank_api_key_fails_before_any_request(config):
    recorder = Recorder()
    blank = type(config)(api_key="", environment="production", base_url=config.base_url)
    client = build(blank, [ok()], recorder)
    with pytest.raises(TogulError, match="api_key is required"):
        await client.evaluate("f")
    assert recorder.count == 0


async def test_invalidate_flag_forces_a_refetch(config):
    recorder = Recorder()
    client = build(config, [ok()], recorder)
    await client.evaluate("f")
    client.invalidate_flag("f")
    await client.evaluate("f")
    assert recorder.count == 2


async def test_works_as_an_async_context_manager(config):
    async with build(config, [ok()]) as client:
        assert (await client.evaluate("f")).enabled is True


async def test_sync_and_async_agree_on_the_same_payload(config):
    import httpx as _httpx

    from togul.client import TogulClient

    payload = ok(value={"theme": "dark"}, value_type="json", flag_key="user_config")

    sync_client = TogulClient(
        config,
        http_client=_httpx.Client(transport=make_transport([payload]), base_url=config.base_url),
    )
    async_client = build(config, [payload])

    assert sync_client.evaluate("user_config") == await async_client.evaluate("user_config")
