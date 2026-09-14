"""FastAPI wiring with real-time SSE cache invalidation."""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI

from togul import AsyncTogulClient, Config
from togul.contrib.fastapi import create_lifespan, get_togul

config = Config(
    api_key=os.environ["TOGUL_API_KEY"],
    environment=os.environ.get("TOGUL_ENVIRONMENT", "production"),
)

app = FastAPI(lifespan=create_lifespan(config))


@app.get("/dashboard")
async def dashboard(
    user_id: str,
    togul: AsyncTogulClient = Depends(get_togul),
) -> dict:
    result = await togul.evaluate("new-dashboard", {"user_id": user_id})
    return {"variant": "new" if result.enabled else "legacy", "reason": result.reason}
