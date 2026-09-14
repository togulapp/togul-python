"""Minimal synchronous usage."""

from __future__ import annotations

import os

from togul import Config, TogulClient

config = Config(
    api_key=os.environ["TOGUL_API_KEY"],
    environment=os.environ.get("TOGUL_ENVIRONMENT", "production"),
)

with TogulClient(config) as client:
    result = client.evaluate("new-dashboard", {"user_id": "u-123", "plan": "premium"})
    print(f"{result.flag_key}: enabled={result.enabled} value={result.value!r} ({result.reason})")
