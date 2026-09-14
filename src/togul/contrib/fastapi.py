from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterator, Callable, Optional, cast

from starlette.requests import Request

from ..async_client import AsyncTogulClient
from ..config import Config
from ..stream import AsyncTogulStreamClient

logger = logging.getLogger("togul")


def create_lifespan(
    config: Config,
    *,
    stream: bool = True,
    client: Optional[AsyncTogulClient] = None,
) -> Callable[[Any], Any]:
    """Build a lifespan context manager for FastAPI or Starlette.

    The client is stored on ``app.state.togul``. When ``stream`` is true an
    ``AsyncTogulStreamClient`` runs as a background task (on
    ``app.state.togul_stream_task``) so SSE events invalidate the cache in
    real time. Both are shut down cleanly on application exit — including an
    injected ``client``, which the lifespan owns for the app's lifetime.

    Usage::

        app = FastAPI(lifespan=create_lifespan(Config(...)))
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        togul = client or AsyncTogulClient(config)
        app.state.togul = togul

        stream_client: Optional[AsyncTogulStreamClient] = None
        task: Optional[asyncio.Task[None]] = None

        if stream:
            stream_client = AsyncTogulStreamClient(config, togul.cache)
            task = asyncio.create_task(stream_client.connect())
            task.add_done_callback(_log_stream_failure)
            app.state.togul_stream_task = task

        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # The stream task already failed on its own — for example a 401
                    # from an API key without `stream` scope. Shutdown must still
                    # close both pools.
                    pass
            if stream_client is not None:
                await stream_client.aclose()
            await togul.aclose()

    return lifespan


def _log_stream_failure(task: asyncio.Task[None]) -> None:
    """Surface an ``AsyncTogulStreamClient`` background task that died on its own.

    Without this, a failed stream task (a 401/403 from an API key that lacks
    ``stream`` scope, for example) fails silently: real-time invalidation
    stops, the cache quietly degrades to TTL-only, and nothing is reported
    until Python's default handler prints "Task exception was never
    retrieved" at garbage-collection time.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "togul: stream task failed, real-time cache invalidation has stopped "
            "(falling back to TTL-only caching): %r",
            exc,
        )


def get_togul(request: Request) -> AsyncTogulClient:
    """FastAPI dependency: ``togul = Depends(get_togul)``."""
    return cast(AsyncTogulClient, request.app.state.togul)
