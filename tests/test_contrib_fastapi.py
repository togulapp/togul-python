from __future__ import annotations

import asyncio
import logging
import types
import warnings

import httpx

from togul.async_client import AsyncTogulClient
from togul.contrib.fastapi import create_lifespan, get_togul
from togul.errors import TogulAPIError

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


def test_get_togul_is_a_real_fastapi_dependency(config):
    """Round-trip ``get_togul`` through FastAPI's actual dependency resolver.

    A prior version of this test called
    ``get_togul(types.SimpleNamespace(app=app))`` directly, bypassing FastAPI's
    resolver entirely. That could never catch the defect where ``request``
    annotated ``Any`` makes FastAPI treat it as a required *query* parameter
    instead of injecting the request object (every ``Depends(get_togul)``
    endpoint returned 422). This drives a real app through ``TestClient`` and
    inspects the generated OpenAPI schema, which is what actually pins the fix:
    a regression back to ``request: Any`` would fail both assertions below.
    """
    with warnings.catch_warnings():
        # Unrelated to this SDK: in this environment starlette.testclient's
        # httpx-fallback import path (httpx2 isn't installed) and anyio's lazy
        # BlockingPortal alias both warn at import time. The import is a
        # one-time, module-cached side effect, not behavior under test.
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient

    from fastapi import Depends, FastAPI

    app = FastAPI(lifespan=create_lifespan(config, stream=False))

    @app.get("/dashboard")
    async def dashboard(togul: AsyncTogulClient = Depends(get_togul)) -> dict:
        return {"has_client": isinstance(togul, AsyncTogulClient)}

    with TestClient(app) as client:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert response.json() == {"has_client": True}

        schema = client.get("/openapi.json").json()
        parameters = schema["paths"]["/dashboard"]["get"].get("parameters", [])
        assert parameters == []  # no bogus required "request" query parameter


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


async def test_shutdown_completes_and_closes_both_pools_when_the_stream_task_fails(
    config, monkeypatch
):
    """FIX 2, part A.

    A real scenario: an API key scoped for ``/evaluate`` but not ``/stream``
    gets a 401 immediately, which ``AsyncTogulStreamClient.connect()``
    re-raises rather than retries. Before the fix, awaiting that failed task
    inside the lifespan's ``finally`` re-raised out of shutdown, so neither
    ``aclose()`` call below it ever ran and both HTTP pools leaked. After the
    fix, shutdown must complete cleanly and both pools must be closed.
    """
    app = FakeApp()
    closed = {"stream": False}

    class FailingStream:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def connect(self) -> None:
            raise TogulAPIError("forbidden", status_code=401)

        async def aclose(self) -> None:
            closed["stream"] = True

    import togul.contrib.fastapi as module

    monkeypatch.setattr(module, "AsyncTogulStreamClient", FailingStream)

    lifespan = create_lifespan(config, stream=True)
    async with lifespan(app):
        # Give the background task a turn to run and fail on its own,
        # well before the lifespan's finally block cancels it.
        await asyncio.sleep(0.05)

    # Shutdown did not raise (the `async with` above completed), and both
    # pools were actually closed rather than leaked.
    assert app.state.togul._http.is_closed
    assert closed["stream"] is True


async def test_stream_task_failure_is_logged(config, monkeypatch, caplog):
    """FIX 2, part B: a stream task that fails on its own must be visible.

    Without the done-callback, this failure is silent until Python's default
    handler prints "Task exception was never retrieved" at GC time. The
    lifespan must log it through the standard `logging` module instead.
    """
    app = FakeApp()

    class FailingStream:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def connect(self) -> None:
            raise TogulAPIError("forbidden", status_code=401)

        async def aclose(self) -> None:
            return None

    import togul.contrib.fastapi as module

    monkeypatch.setattr(module, "AsyncTogulStreamClient", FailingStream)

    lifespan = create_lifespan(config, stream=True)
    with caplog.at_level(logging.ERROR, logger="togul"):
        async with lifespan(app):
            await asyncio.sleep(0.05)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "real-time" in errors[0].message.lower() or "real-time" in errors[0].getMessage().lower()


async def test_cancellation_is_not_logged_as_a_failure(config, monkeypatch, caplog):
    """A clean shutdown (task cancelled, not failed) must not be logged as an error."""
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
    with caplog.at_level(logging.ERROR, logger="togul"):
        async with lifespan(app):
            await asyncio.wait_for(connected.wait(), timeout=1.0)

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
