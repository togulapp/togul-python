from __future__ import annotations

import asyncio
import types

import httpx

from togul.async_client import AsyncTogulClient
from togul.contrib.fastapi import create_lifespan, get_togul

from .conftest import make_transport, ok


class FakeApp:
    def __init__(self) -> None:
        self.state = types.SimpleNamespace()


async def test_lifespan_sets_and_closes_the_client(config):
    app = FakeApp()
    lifespan = create_lifespan(config, stream=False)

    async with lifespan(app):
        assert isinstance(app.state.togul, AsyncTogulClient)
        client = app.state.togul

    assert client._http.is_closed


async def test_lifespan_accepts_an_injected_client(config):
    app = FakeApp()
    injected = AsyncTogulClient(
        config,
        http_client=httpx.AsyncClient(transport=make_transport([ok()]), base_url=config.base_url),
    )
    lifespan = create_lifespan(config, stream=False, client=injected)

    async with lifespan(app):
        assert app.state.togul is injected
        assert (await app.state.togul.evaluate("f")).enabled is True


async def test_get_togul_reads_the_client_off_the_app(config):
    app = FakeApp()
    app.state.togul = "sentinel"
    request = types.SimpleNamespace(app=app)
    assert get_togul(request) == "sentinel"


async def test_stream_task_is_created_and_cancelled_on_shutdown(config, monkeypatch):
    app = FakeApp()
    connected = asyncio.Event()

    class FakeStream:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def connect(self) -> None:
            connected.set()
            await asyncio.sleep(3600)

        async def aclose(self) -> None:
            return None

    import togul.contrib.fastapi as module

    monkeypatch.setattr(module, "AsyncTogulStreamClient", FakeStream)

    lifespan = create_lifespan(config, stream=True)
    async with lifespan(app):
        await asyncio.wait_for(connected.wait(), timeout=1.0)
        task = app.state.togul_stream_task
        assert not task.done()

    assert task.cancelled()
