from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator, Callable, Optional, cast

from ..async_client import AsyncTogulClient
from ..config import Config
from ..stream import AsyncTogulStreamClient


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
            app.state.togul_stream_task = task

        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if stream_client is not None:
                await stream_client.aclose()
            await togul.aclose()

    return lifespan


def get_togul(request: Any) -> AsyncTogulClient:
    """FastAPI dependency: ``togul = Depends(get_togul)``."""
    return cast(AsyncTogulClient, request.app.state.togul)
